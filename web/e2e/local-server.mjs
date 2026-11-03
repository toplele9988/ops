// Built frontend only; binds loopback, never imports or proxies a backend.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";

export async function startLocalServer() {
  const dist = fileURLToPath(new URL("../dist/", import.meta.url));
  await readFile(resolve(dist, "index.html"));
  const server = createServer(async (request, response) => {
    response.setHeader("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; frame-ancestors 'none'");
    response.setHeader("Cache-Control", "no-store");
    try {
      const path = new URL(request.url, "http://localhost").pathname;
      if (request.method !== "GET" || !path.startsWith("/app/")) { response.writeHead(404).end(); return; }
      const asset = path.startsWith("/app/assets/");
      const file = asset ? resolve(dist, decodeURIComponent(path.slice(5))) : resolve(dist, "index.html");
      if (!file.startsWith(dist.endsWith(sep) ? dist : dist + sep)) throw new Error("Invalid path");
      const content = await readFile(file);
      response.setHeader("Content-Type", ({ ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml" })[extname(file)] ?? "application/octet-stream");
      response.end(content);
    } catch { response.writeHead(404).end(); }
  });
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  return { base: `http://127.0.0.1:${server.address().port}`, close: () => new Promise((resolve) => server.close(resolve)) };
}
