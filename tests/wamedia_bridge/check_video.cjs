// 使用临时前端副本和真实 React 渲染，不启动服务器或获取附件。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { Module, createRequire } = require('node:module');

const root = path.resolve(process.argv[2]);
const fromWeb = createRequire(path.join(root, 'package.json'));
const ts = fromWeb('typescript');
const React = fromWeb('react');
const { renderToStaticMarkup } = fromWeb('react-dom/server');

function load(name) {
  const filename = path.join(root, 'src/ui', name + '.tsx');
  const unit = new Module(filename);
  unit.filename = filename;
  unit.require = (name) => name === './bits' ? load('bits') : fromWeb(name);
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  unit._compile(code, filename);
  return unit.exports;
}

const { BlockView } = load('Blocks');
const render = (block) => renderToStaticMarkup(React.createElement(BlockView, { block }));
const video = render({ type: 'video', media: 'fictional.signed', duration_s: 65, size_bytes: 2000000,
                       thumbnail: 'data:image/jpeg;base64,ZmFrZQ==' });
assert.match(video, /播放视频/);
assert.match(video, /1分5秒/);
assert.match(video, /2\.0 MB/);
assert.match(video, /下载视频/);
assert.doesNotMatch(video, /<video|<img/);
assert.match(render({ type: 'image', media: 'fictional.signed' }), /<img/);
assert.match(render({ type: 'file', media: 'fictional.signed', name: '虚构文件', size_bytes: 1234 }), /download=""/);
console.log('视频占位、图片预览和文件下载渲染：3 项通过，0 项失败');
