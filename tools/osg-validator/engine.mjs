import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
import * as lint from '@abaplint/core';
import {Transpiler} from '@abaplint/transpiler';
import {HandleABAP} from '@abaplint/transpiler/build/src/handlers/handle_abap.js';
import {UnitTest} from '@abaplint/transpiler/build/src/unit_test.js';
import {bootstrap, core, root, revision} from './bootstrap.mjs';
import {ddlToTable} from './ddl.mjs';

export const rules = {parser_error:true, check_syntax:true, unknown_types:true, implement_methods:true, begin_end_names:true};
export const config = {global:{files:'/**/*.*'},syntax:{version:lint.Version.Cloud,errorNamespace:'.'},rules};
export const clean = value => String(value).replaceAll(root, '<validator>').replace(/file:\/\/\/[^\s)]+/g,'<module>').slice(0,2500);
function files(dir) {
  return fs.readdirSync(dir,{withFileTypes:true}).flatMap(f=>f.isDirectory()?files(path.join(dir,f.name)):[path.join(dir,f.name)]);
}
export async function createEngine() {
  bootstrap();
  const library = files(path.join(core,'src')).filter(f=>!f.includes('.testclasses.') && /\.(abap|xml)$/.test(f)).map(f=>new lint.MemoryFile(path.basename(f),fs.readFileSync(f,'utf8')));
  const tableDir = path.resolve(root,'../../dataset/abap_tables');
  const tables = fs.readdirSync(tableDir).filter(f=>f.endsWith('.abap')).map(f=>ddlToTable(fs.readFileSync(path.join(tableDir,f),'utf8')));
  // Compile the immutable library once; generated code is never evaluated here.
  const cache = path.join(root,'.cache',`compiled-${revision}`);
  if (!fs.existsSync(path.join(cache,'ready.json'))) {
    fs.mkdirSync(cache,{recursive:true});
    const reg = new lint.Registry().addFiles(library).addFiles(tables.map(t=>new lint.MemoryFile(t.filename,t.contents)));
    const output = await new Transpiler({addCommonJS:true}).run(reg);
    for (const f of output.objects) fs.writeFileSync(path.join(cache,path.basename(f.filename)),f.chunk.getCode());
    fs.writeFileSync(path.join(cache,'library.mjs'),output.initializationScript.replace('globalThis.abap = new runtime.ABAP();','globalThis.abap ??= new runtime.ABAP();'));
    fs.writeFileSync(path.join(cache,'schema.json'),JSON.stringify(output.databaseSetup));
    fs.writeFileSync(path.join(cache,'ready.json'),JSON.stringify({revision}));
  }
  const reg = new lint.Registry(new lint.Config(JSON.stringify(config))).addDependencies(library).addFiles(tables.map(t=>new lint.MemoryFile(t.filename,t.contents)));
  reg.parse();
  let previous = [];
  return async function validate(input, notify = ()=>{}) {
    const result = {syntax:null,activation:null,unit_syntax:null,unit:null,stage:null,errors:[],test_count:0};
    const fail = (stage, errors) => Object.assign(result,{[stage]:false,stage,errors:errors.map(clean)});
    const check = () => reg.findIssues().filter(i=>i.getFilename().startsWith(name+'.clas.')).map(i=>`${i.getKey()}: ${i.getMessage()} (line ${i.getStart().getRow()})`);
    for (const f of previous) reg.removeFile(f); previous=[];
    const name = input.name.toLowerCase();
    if (!/^z\w+$/.test(name)) return fail('syntax',['Invalid class identifier']);
    const add = (suffix,source) => {const f=new lint.MemoryFile(`${name}.clas.${suffix}`,source);reg.addFile(f);previous.push(f);};
    add('abap',input.source);
    let errors = check(); if(errors.length) return fail('syntax',errors);
    result.syntax=true; notify(result);
    let compiled;
    try {compiled = new HandleABAP({addCommonJS:true}).runObject(reg.getObject('CLAS',name),reg);}
    catch(e) {return fail('activation',[e.message]);}
    result.activation=true;notify(result);
    add('testclasses.abap',input.tests);
    errors=check();if(errors.length)return fail('unit_syntax',errors);
    result.unit_syntax=true;notify(result);
    try {compiled=new HandleABAP({addCommonJS:true}).runObject(reg.getObject('CLAS',name),reg);}
    catch(e){return fail('activation',[e.message]);}
    const script = new UnitTest().unitTestScript(reg);
    result.test_count=(script.match(/"skip":false/g)||[]).length;
    if(!result.test_count)return fail('unit',['No ABAP Unit test methods discovered']);
    const runs=path.join(root,'.runs');fs.mkdirSync(runs,{recursive:true});
    const dir=fs.mkdtempSync(path.join(runs,`answer-${process.pid}-`));
    try {
      for(const f of fs.readdirSync(cache))if(f.endsWith('.mjs'))fs.symlinkSync(path.join(cache,f),path.join(dir,f));
      for(const f of compiled)fs.writeFileSync(path.join(dir,path.basename(f.filename)),f.chunk.getCode());
      const schema=JSON.parse(fs.readFileSync(path.join(cache,'schema.json'),'utf8'));
      fs.writeFileSync(path.join(dir,'init.mjs'),`import runtime from '@abaplint/runtime';\nimport {SQLiteDatabaseClient} from '@abaplint/database-sqlite';\nglobalThis.abap=new runtime.ABAP();\nconst db=new SQLiteDatabaseClient(); await db.connect(); abap.context.databaseConnections.DEFAULT=db;\nawait db.execute(${JSON.stringify(schema.schemas.sqlite)});\nawait db.execute(${JSON.stringify(schema.insert)});\nawait import('./library.mjs');\nawait import('./${name}.clas.mjs');\n`);
      fs.writeFileSync(path.join(dir,'run.mjs'),script);
      const run=await new Promise(resolve=>{
        const child=spawn(process.execPath,['--max-old-space-size=256',path.join(dir,'run.mjs')],{cwd:dir,env:{PATH:process.env.PATH},stdio:['ignore','pipe','pipe']});
        let output='';const append=b=>{output=(output+b.toString()).slice(-16000);};child.stdout.on('data',append);child.stderr.on('data',append);
        let killedReason;
        const timer=setTimeout(()=>{killedReason='Answer exceeded 30 s wall timeout';child.kill('SIGKILL');},30000);
        const memory=setInterval(()=>{
          try {const status=fs.readFileSync(`/proc/${child.pid}/status`,'utf8');
            if(Number(status.match(/VmRSS:\s+(\d+)/)?.[1])>384*1024){killedReason='Answer exceeded 384 MiB resident memory cap';child.kill('SIGKILL');}
          } catch {} // Child already exited.
        },100);
        child.on('error',e=>{clearTimeout(timer);clearInterval(memory);resolve({code:1,output:e.message});});
        child.on('close',(code,signal)=>{clearTimeout(timer);clearInterval(memory);resolve({code,output:signal?(killedReason || 'Answer terminated by signal '+signal):output});});
      });
      if(run.code!==0)return fail('unit',[run.output]);result.unit=true;return result;
    }finally {fs.rmSync(dir,{recursive:true,force:true});}
  };
}
