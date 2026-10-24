import {execFileSync} from 'node:child_process';
import {existsSync, mkdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
export const root = path.dirname(fileURLToPath(import.meta.url));
export const core = path.join(root, '.cache/open-abap-core');
export const revision = '8b397be863e805182c32e4bbb8bedbd291619f58';
export function bootstrap() {
  mkdirSync(path.dirname(core), {recursive: true});
  if (!existsSync(path.join(core, '.git'))) {
    execFileSync('git', ['init', '-q', core]);
    execFileSync('git', ['-C', core, 'remote', 'add', 'origin', 'https://github.com/open-abap/open-abap-core.git']);
  }
  const sha = execFileSync('git', ['-C', core, 'rev-parse', '--verify', 'HEAD'], {encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore']}).trim();
  if (sha !== revision) throw new Error('Unexpected library revision; remove .cache and bootstrap again');
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  mkdirSync(path.dirname(core), {recursive:true});
  if (!existsSync(path.join(core, '.git'))) {
    execFileSync('git', ['init', '-q', core]);
    execFileSync('git', ['-C', core, 'remote', 'add', 'origin', 'https://github.com/open-abap/open-abap-core.git']);
    execFileSync('git', ['-C', core, 'fetch', '--depth=1', 'origin', revision], {stdio:'inherit'});
    execFileSync('git', ['-C', core, 'checkout', '--detach', 'FETCH_HEAD'], {stdio:'inherit'});
  }
  bootstrap();
}
