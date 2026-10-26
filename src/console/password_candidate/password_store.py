"""Two equal password accounts with local bootstrap; synthetic testing only."""
import fcntl,hashlib,hmac,os,re,secrets,sqlite3,stat,time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

APP_ID=0x49323231
USER=re.compile(r'[a-z][a-z0-9_.-]{2,63}\Z')
def digest(v):return hashlib.sha256(v.encode('ascii')).hexdigest()
def password_hash(password,salt):
    if not isinstance(password,str) or not 5<=len(password)<=256 or len(password.encode())>1024:raise ValueError('invalid_password')
    return hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,dklen=32)
@dataclass(frozen=True)
class Identity:
    username:str
    expires:float
class PasswordStore:
    def __init__(self,path,clock=time.time,*,bootstrap=False):
        self.path=Path(path);self.clock=clock
        p=self.path.parent.lstat()
        if not stat.S_ISDIR(p.st_mode) or p.st_uid!=os.geteuid() or stat.S_IMODE(p.st_mode)!=0o700:raise PermissionError('unsafe_directory')
        lock=os.open(self.path.parent/(self.path.name+'.init.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        try:
            s=os.fstat(lock)
            if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.geteuid() or stat.S_IMODE(s.st_mode)!=0o600 or s.st_nlink!=1:raise PermissionError('unsafe_initialization_lock')
            fcntl.flock(lock,fcntl.LOCK_EX)
            self._initialize(bootstrap)
        finally:os.close(lock)
    def _initialize(self,bootstrap):
        try:
            fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600);created=True
        except FileExistsError:
            fd=os.open(self.path,os.O_RDWR|os.O_NOFOLLOW);created=False
        try:
            s=os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.geteuid() or stat.S_IMODE(s.st_mode)!=0o600 or s.st_nlink!=1:raise PermissionError('unsafe_database')
        finally:os.close(fd)
        with sqlite3.connect(self.path) as c:
            if created:
                c.executescript(f'''PRAGMA application_id={APP_ID};PRAGMA user_version=2;
                CREATE TABLE users(username TEXT PRIMARY KEY,salt BLOB NOT NULL,hash BLOB NOT NULL,failed INTEGER NOT NULL,locked_until REAL NOT NULL);
                CREATE TABLE enrollments(code_hash TEXT PRIMARY KEY,username TEXT NOT NULL,expires REAL NOT NULL,used INTEGER NOT NULL);
                CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL REFERENCES users(username) ON UPDATE CASCADE,expires REAL NOT NULL,revoked INTEGER NOT NULL,audience TEXT NOT NULL CHECK(audience IN ('https','lan_http')));
                CREATE TABLE audit(id INTEGER PRIMARY KEY,at REAL NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,result TEXT NOT NULL);
                CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'immutable'); END;
                CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'immutable'); END;''')
            else:
                if c.execute('pragma application_id').fetchone()[0]!=APP_ID or c.execute('pragma user_version').fetchone()[0]!=2:raise ValueError('wrong_database')
                required={'users','enrollments','sessions','audit','audit_no_update','audit_no_delete'}
                if not required.issubset({r[0] for r in c.execute('select name from sqlite_master')}):raise ValueError('schema_invalid')
                columns={
                    'users':['username','salt','hash','failed','locked_until'],
                    'enrollments':['code_hash','username','expires','used'],
                    'sessions':['token_hash','username','expires','revoked','audience'],
                    'audit':['id','at','actor','action','result'],
                }
                for table,expected in columns.items():
                    if [r[1] for r in c.execute('pragma table_info('+table+')')]!=expected:raise ValueError('schema_invalid')
                fk=c.execute('pragma foreign_key_list(sessions)').fetchall()
                if not any(r[2]=='users' and r[3]=='username' and r[4]=='username' and r[5]=='CASCADE' for r in fk):raise ValueError('schema_invalid')
                for name,verb in [('audit_no_update','UPDATE'),('audit_no_delete','DELETE')]:
                    row=c.execute("select sql from sqlite_master where type='trigger' and name=?",(name,)).fetchone()
                    if not row or ('BEFORE '+verb+' ON audit') not in row[0] or "RAISE(ABORT,'immutable')" not in row[0]:raise ValueError('schema_invalid')
        if bootstrap:raise ValueError('explicit_provision_required')
    @contextmanager
    def conn(self):
        c=sqlite3.connect(self.path,timeout=5);c.execute('pragma foreign_keys=on')
        try:
            with c:yield c
        finally:c.close()
    def provision(self,user,password):
        """Local only: set/reset either of two accounts; revokes its sessions."""
        if not isinstance(user,str) or not USER.fullmatch(user):raise ValueError('invalid_user')
        salt=os.urandom(16);pw=password_hash(password,salt)
        with self.conn() as c:
            c.execute('begin immediate')
            if not c.execute('select 1 from users where username=?',(user,)).fetchone() and c.execute('select count(*) from users').fetchone()[0]>=2:raise ValueError('two_accounts_only')
            c.execute('insert into users values(?,?,?,0,0) on conflict(username) do update set salt=excluded.salt,hash=excluded.hash,failed=0,locked_until=0',(user,salt,pw))
            c.execute('update sessions set revoked=1 where username=?',(user,))
            c.execute('insert into audit(at,actor,action,result) values(?,?,?,?)',(self.clock(),user,'local_password_set','ok'))
    def login(self,user,password,audience='https'):
        if audience not in ('https','lan_http'):raise ValueError('invalid_audience')
        now=self.clock()
        with self.conn() as c:
            snapshot=c.execute('select salt,hash from users where username=?',(user,)).fetchone()
        salt=snapshot[0] if snapshot else bytes(16)
        input_valid=True
        try:calculated=password_hash(password,salt)
        except (ValueError,UnicodeError):
            input_valid=False;calculated=password_hash('invalid input password',salt)
        with self.conn() as c:
            c.execute('begin immediate')
            row=c.execute('select salt,hash,failed,locked_until from users where username=?',(user,)).fetchone()
            valid=bool(input_valid and row and snapshot and row[:2]==snapshot and row[3]<=now and hmac.compare_digest(calculated,row[1]))
            if not valid:
                if row and row[3]<=now:
                    failed=row[2]+1;c.execute('update users set failed=?,locked_until=? where username=?',(0 if failed>=10 else failed,now+900 if failed>=10 else 0,user))
                c.execute('insert into audit(at,actor,action,result) values(?,?,?,?)',(now,user if row else 'unknown','login','denied'))
                return None
            c.execute('update users set failed=0,locked_until=0 where username=?',(user,))
            token=secrets.token_urlsafe(32);c.execute('insert into sessions values(?,?,?,0,?)',(digest(token),user,now+8*3600,audience))
            c.execute('insert into audit(at,actor,action,result) values(?,?,?,?)',(now,user,'login','ok'));return token
    def resolve(self,token,audience='https'):
        if audience not in ('https','lan_http'):return None
        try:key=digest(token)
        except (UnicodeError,AttributeError):return None
        with self.conn() as c:
            row=c.execute('select username,expires from sessions where token_hash=? and revoked=0 and expires>? and audience=?',(key,self.clock(),audience)).fetchone()
            return Identity(*row) if row else None
    def list_accounts(self,token,audience='https'):
        """Return only account identifiers after revalidating the calling session."""
        if audience not in ('https','lan_http'):return None
        try:key=digest(token)
        except (UnicodeError,AttributeError,TypeError):return None
        with self.conn() as c:
            c.execute('begin')
            row=c.execute('select username from sessions where token_hash=? and revoked=0 and expires>? and audience=?',(key,self.clock(),audience)).fetchone()
            if not row:return None
            return [r[0] for r in c.execute('select username from users order by username')]
    def logout(self,token):
        try:key=digest(token)
        except (UnicodeError,AttributeError):return
        with self.conn() as c:
            c.execute('begin immediate');row=c.execute('select username from sessions where token_hash=? and revoked=0',(key,)).fetchone()
            if row:c.execute('update sessions set revoked=1 where token_hash=?',(key,));c.execute('insert into audit(at,actor,action,result) values(?,?,?,?)',(self.clock(),row[0],'logout','ok'))
    def audit_action(self,username,method,path,result):
        if method not in ('POST','PUT','PATCH','DELETE') or not re.fullmatch(r'/[A-Za-z0-9_./-]{0,200}',path) or result not in ('attempt','success','denied','error'):raise ValueError('invalid_audit')
        with self.conn() as c:c.execute('insert into audit(at,actor,action,result) values(?,?,?,?)',(self.clock(),username,method+' '+path,result))
    def _recheck_actor(self,c,token,audience,expected):
        row=c.execute('select u.username,u.salt,u.hash from sessions s join users u on u.username=s.username where s.token_hash=? and s.audience=? and s.revoked=0 and s.expires>?',(digest(token),audience,self.clock())).fetchone()
        if not row or row!=expected:raise ValueError('denied')
        return row[0]
    def _actor_snapshot(self,token,audience,current_password):
        with self.conn() as c:
            row=c.execute('select u.username,u.salt,u.hash from sessions s join users u on u.username=s.username where s.token_hash=? and s.audience=? and s.revoked=0 and s.expires>?',(digest(token),audience,self.clock())).fetchone()
        salt=row[1] if row else bytes(16)
        try:pw=password_hash(current_password,salt);valid=True
        except (ValueError,UnicodeError):pw=password_hash('invalid input password',salt);valid=False
        if not valid or not row or not hmac.compare_digest(row[2],pw):raise ValueError('denied')
        return row
    def update_self(self,token,audience,current_password,new_username,new_password):
        if not isinstance(new_username,str) or not USER.fullmatch(new_username):raise ValueError('invalid_user')
        actor=self._actor_snapshot(token,audience,current_password);salt=os.urandom(16);pw=password_hash(new_password,salt)
        with self.conn() as c:
            c.execute('begin immediate');old=self._recheck_actor(c,token,audience,actor)
            if new_username!=old and c.execute('select 1 from users where username=?',(new_username,)).fetchone():raise ValueError('user_exists')
            c.execute('update users set username=?,salt=?,hash=?,failed=0,locked_until=0 where username=?',(new_username,salt,pw,old))
            c.execute('update sessions set revoked=1 where username=?',(new_username,))
            c.execute('insert into audit(at,actor,action,result) values(?,?,?,?)',(self.clock(),old,'self_credentials_changed','ok'))
    def create_second(self,token,audience,current_password,new_username,new_password):
        if not isinstance(new_username,str) or not USER.fullmatch(new_username):raise ValueError('invalid_user')
        actor=self._actor_snapshot(token,audience,current_password);salt=os.urandom(16);pw=password_hash(new_password,salt)
        with self.conn() as c:
            c.execute('begin immediate');who=self._recheck_actor(c,token,audience,actor)
            if c.execute('select 1 from users where username=?',(new_username,)).fetchone():raise ValueError('user_exists')
            if c.execute('select count(*) from users').fetchone()[0]!=1:raise ValueError('user_limit')
            c.execute('insert into users values(?,?,?,0,0)',(new_username,salt,pw))
            c.execute('insert into audit(at,actor,action,result) values(?,?,?,?)',(self.clock(),who,'second_account_created','ok'))
