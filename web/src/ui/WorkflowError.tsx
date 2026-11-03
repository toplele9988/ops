export function WorkflowError({ message, refresh }: { message: string; refresh: () => void }) {
  return <div className="group" role="alert" style={{ padding: 16 }}><p>{message}</p><button type="button" className="btn" onClick={refresh}>重新读取</button></div>;
}
