#!/usr/bin/env node

'use strict';

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const packageRoot = path.resolve(__dirname, '..');
const distDir = path.join(packageRoot, 'dist');
const esmTempDir = path.join(packageRoot, '.esm-build');
const tsc = path.join(packageRoot, 'node_modules', 'typescript', 'bin', 'tsc');

function rewriteRelativeSpecifiers(source) {
  const withStaticImports = source.replace(
    /(\bfrom\s+['"])(\.{1,2}\/[^'"]+)(['"])/g,
    (_match, prefix, specifier, suffix) =>
      `${prefix}${path.extname(specifier) ? specifier : `${specifier}.mjs`}${suffix}`,
  );
  return withStaticImports.replace(
    /(\bimport\s*\(\s*['"])(\.{1,2}\/[^'"]+)(['"]\s*\))/g,
    (_match, prefix, specifier, suffix) =>
      `${prefix}${path.extname(specifier) ? specifier : `${specifier}.mjs`}${suffix}`,
  );
}

function emitMjsTree(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const sourcePath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      emitMjsTree(sourcePath);
      continue;
    }
    if (!entry.name.endsWith('.js')) continue;

    const relativePath = path.relative(esmTempDir, sourcePath);
    const targetPath = path.join(distDir, relativePath.replace(/\.js$/, '.mjs'));
    fs.mkdirSync(path.dirname(targetPath), { recursive: true });
    const source = fs.readFileSync(sourcePath, 'utf8');
    fs.writeFileSync(targetPath, rewriteRelativeSpecifiers(source));
  }
}

async function main() {
  fs.rmSync(esmTempDir, { recursive: true, force: true });
  fs.mkdirSync(esmTempDir, { recursive: true });

  try {
    const result = spawnSync(
      process.execPath,
      [
        tsc,
        '--module',
        'ES2020',
        '--moduleResolution',
        'Node',
        '--outDir',
        esmTempDir,
        '--declaration',
        'false',
        '--declarationMap',
        'false',
        '--sourceMap',
        'false',
      ],
      { cwd: packageRoot, stdio: 'inherit' },
    );
    if (result.error) throw result.error;
    if (result.status !== 0) process.exit(result.status ?? 1);

    emitMjsTree(esmTempDir);
    fs.copyFileSync(path.join(distDir, 'index.js'), path.join(distDir, 'browser.js'));
    fs.copyFileSync(path.join(distDir, 'index.mjs'), path.join(distDir, 'browser.mjs'));

    const commonJs = require(path.join(distDir, 'index.js'));
    const browserCommonJs = require(path.join(distDir, 'browser.js'));
    const esm = await import(pathToFileURL(path.join(distDir, 'index.mjs')).href);
    const browserEsm = await import(pathToFileURL(path.join(distDir, 'browser.mjs')).href);
    for (const [label, moduleShape] of [
      ['CommonJS', commonJs],
      ['browser CommonJS', browserCommonJs],
      ['ESM', esm],
      ['browser ESM', browserEsm],
    ]) {
      if (moduleShape.VERSION !== '1.0.0' || typeof moduleShape.TokenPak !== 'function') {
        throw new Error(`${label} export smoke failed`);
      }
    }
  } finally {
    fs.rmSync(esmTempDir, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
