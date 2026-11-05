"""Model calls from inside the console.

`ai-task` switches to the imgw user with sudo before calling the gateway. The
console already runs as imgw and has no sudo rule for itself, so the CLI fails
with gateway_unavailable there. When already imgw, send the same request to the
gateway directly; otherwise behave exactly like subprocess.run.
"""
import json
import os
import pwd
import re
import subprocess

GATEWAY = '/usr/local/libexec/imgw-ai-task'


def _is_imgw():
    try:
        return pwd.getpwuid(os.geteuid()).pw_name == 'imgw'
    except KeyError:
        return False


def run(args, *, input=None, text=True, capture_output=True, timeout=None, check=False):
    args = list(args)
    if (len(args) == 5 and os.path.basename(args[0]) == 'ai-task' and args[1] == 'run'
            and args[3] == '--model' and _is_imgw()):
        request = json.dumps(dict(action='run', provider=args[2], model=args[4], task=input or ''), ensure_ascii=False)
        return subprocess.run([GATEWAY], input=request, text=True, capture_output=True, timeout=timeout, check=False)
    return subprocess.run(args, input=input, text=text, capture_output=capture_output, timeout=timeout, check=check)


def unfence(text):
    """Models sometimes wrap the JSON answer in a Markdown code block."""
    text = text.strip()
    match = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', text, re.S)
    return match.group(1) if match else text
