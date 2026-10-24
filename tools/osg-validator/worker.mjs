// JSON-lines protocol: one request and response per line. Only stdout is protocol.
import readline from 'node:readline';
import {createEngine,clean} from './engine.mjs';
const validate=await createEngine();
for await (const line of readline.createInterface({input:process.stdin,crlfDelay:Infinity})) {
  try {const result=await validate(JSON.parse(line),r=>process.stderr.write(JSON.stringify({progress:r})+'\n'));process.stdout.write(JSON.stringify(result)+'\n');}
  catch(e){process.stdout.write(JSON.stringify({infrastructure_error:clean(e.message)})+'\n');}
}
