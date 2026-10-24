// Minimal diagnostic reproductions; these intentionally fail on the pinned runtime.
import fs from 'node:fs';
import path from 'node:path';
import {root} from './bootstrap.mjs';
import {createEngine} from './engine.mjs';
const validate=await createEngine();
const results=[];
for(const file of fs.readdirSync(path.join(root,'gaps')).filter(f=>f.endsWith('.clas.abap'))) {
  const source=fs.readFileSync(path.join(root,'gaps',file),'utf8');
  const name=file.split('.')[0];
  const result=await validate({name,source,tests:fs.readFileSync(path.join(root,'gaps',name+'.clas.testclasses.abap'),'utf8')});
  const expected=source.match(/Expected failing stage: (\w+)/)[1];
  results.push({file:'gaps/'+file,expected_stage:expected,observed_stage:result.stage,errors:result.errors.map(e=>(e.length>900 ? e.slice(0,180)+'\n...\n'+e.slice(-700) : e).replace(/answer-\d+-[^/]+/g,'<answer>'))});
  if(result.stage!==expected)process.exitCode=1;
}
fs.writeFileSync(path.join(root,'gaps-summary.json'),JSON.stringify(results,null,2)+'\n');
console.log(JSON.stringify(results,null,2));
