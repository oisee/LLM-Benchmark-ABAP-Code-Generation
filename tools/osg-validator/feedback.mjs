// Adapter-only observation: assertions still execute the pinned implementation.
// No dependency source is changed and no assertion comparison is reimplemented.
export function safeText(value) {
  return String(value ?? '').split('\n').filter(s=>!/^\s*at\s/.test(s)).join('\n')
    .replace(/(?:file:\/\/\/|\/)[\w.@%+\-/]+\.(?:m?js|abap)(?::\d+(?::\d+)?)?/g,'<source>')
    .replace(/(?:\/[^\s:'"<>]+){2,}/g,'<source>').slice(0,2500);
}
function displayValue(value) {
  const text=String(value);
  return text.length>2500 ? text.slice(0,2500)+'[truncated]' : text;
}
export function abapValue(value, depth = 0) {
  if (depth > 8) return '...';
  if (value == null) return '';
  if (value.getPointer) return abapValue(value.getPointer(), depth+1);
  if (value.array) return value.array().map(v=>abapValue(v,depth+1)).join(', ');
  if (/^(Integer|Integer8|Float|Packed|DecFloat34)$/.test(value.constructor?.name)) {
    const raw=String(value.get());
    const [whole, fraction]=raw.replace(/^-/, '').split('.');
    return whole.replace(/\B(?=(\d{3})+(?!\d))/g,'.')+(fraction?','+fraction:'')+(raw.startsWith('-')?'-':'');
  }
  if (value.get) return abapValue(value.get(),depth+1);
  if (typeof value === 'object') return Object.values(value).map(v=>abapValue(v,depth+1)).join(', ');
  // ABAP booleans remain X/space; character padding and NUMC zeroes are preserved.
  return typeof value==='string' && /^ +$/.test(value) ? '' : displayValue(value);
}
function dereference(v) {return v?.getPointer ? dereference(v.getPointer()) : v;}
function rowInfo(row) {
  row=dereference(row);
  const name=row?.constructor?.name;
  const known={Integer:[4,'TYPE=I'],Integer8:[8,'TYPE=INT8'],String:[8,'TYPE=STRING'],Float:[8,'TYPE=F']};
  if (known[name]) return known[name];
  if (name==='Structure') {
    const fields=Object.values(row.get());
    return [fields.reduce((n,f)=>n+rowInfo(f)[0],0),row.getQualifiedName?.() || 'TYPE=STRUCTURE'];
  }
  return [row?.getLength?.() || 0,'TYPE='+({Character:'C',Numc:'N',Date:'D',Time:'T',Hex:'X'}[name] || name || 'ANY')];
}
function tableSummary(table) {
  const [length,type]=rowInfo(table.getRowType());
  return `${table.constructor.name==='HashedTable'?'H':'S'}-Table[${table.array().length}x${length}] of ${type}`;
}
export function comparison(actual, expected) {
  actual=dereference(actual); expected=dereference(expected);
  if (actual?.array && expected?.array) {
    const a=actual.array(), e=expected.array();
    const sa=tableSummary(actual),se=tableSummary(expected);
    let detail=sa===se ? `Table type [${se}]` : `Different values\nExpected [${se}]\nActual [${sa}]`;
    if (!a.length || !e.length) return detail+'\n'+(!a.length?'Actual':'Expected')+' table is empty';
    const display=row=>/^(Integer|Integer8|Float|Packed)$/.test(dereference(row)?.constructor?.name)?` ${abapValue(row)}${abapValue(row).endsWith('-')?'':' '}`:abapValue(row);
    for (let i=0;i<a.length;i++) {
      if (abapValue(a[i])===abapValue(e[i])) continue;
      const j=e.findIndex(row=>abapValue(row)===abapValue(a[i]));
      if (j>=0) detail+=`\nSame line [${display(a[i])}] at different table index: Expected [${j+1}], Actual [${i+1}]`;
      else detail+=`\nTable line at index [${i+1}] of actual table not contained in expected table:\n[${display(a[i])}]`;
    }
    for (let i=0;i<e.length;i++) if (!a.some(row=>abapValue(row)===abapValue(e[i])))
      detail+=`\nTable line at index [${i+1}] of expected table not contained in actual table:\n[${display(e[i])}]`;
    return detail;
  }
  const a=abapValue(actual),e=abapValue(expected);
  // SAP uses a string-prefix diagnostic for long character comparisons.
  if (actual?.constructor?.name==='String' && expected?.constructor?.name==='String' && Math.max(a.length,e.length)>20) {
    let i=0; while(i<Math.min(a.length,e.length) && a[i]===e[i]) i++;
    return i===0 ? `Character string differs from the beginning\nExpected [${e}] Actual [${a}]` :
      `Character string different as of position ${i+1}\nExpected [${e}]\nActual [${a}]`;
  }
  return `Different values\nExpected [${e}] Actual [${a}]`;
}
export function entry(title, kind, severity, details = [], stack = []) {
  return {title,kind,severity,stack,details};
}
export function runtimeEntry(message) {
  return entry('Failed due to a runtime abortion','runtimeAbortion','fatal',[`[${safeText(message)}]`]);
}
export function timeoutEntries(count = 1) {
  const title='Maximum allowed duration of 60 seconds exceeded';
  return [entry(title,'warning','tolerable'), ...Array.from({length:Math.max(1,count)},()=>entry(title,'abortion','tolerable'))];
}
export function installAssertions() {
  const lookup=abap.operators.tableExpression;
  abap.operators={...abap.operators, tableExpression:async function(source, options) {
    try {return await lookup(source, options);}
    catch(error) {
      if (options.index && error?.constructor?.name?.toUpperCase()==='CX_SY_ITAB_LINE_NOT_FOUND')
        error.osgExceptionText=`A row with the index ${abapValue(options.index)} is not in the table.`;
      throw error;
    }
  }};
  const cls=abap.Classes.CL_ABAP_UNIT_ASSERT;
  let depth=0;
  for (const method of Object.getOwnPropertyNames(cls).filter(n=>n.startsWith('assert_') || n==='fail' || n==='abort')) {
    const original=cls[method];
    if (typeof original!=='function') continue;
    cls[method]=async function(args={}) {
      const outer=depth++ === 0;
      try { return await original.call(this,args); }
      catch(error) {
        if (outer && error && typeof error==='object') {
          error.osgAssertion={method:method.toUpperCase(),msg:abapValue(args.msg),
            actual:abapValue(args.act ?? args.number ?? args.text),
            expected:abapValue(args.exp ?? (method==='assert_true'?'X':method==='assert_false'?' ':undefined)),
            comparison:method==='assert_equals'?comparison(args.act,args.exp):null};
        }
        throw error;
      } finally { depth--; }
    };
  }
}
export async function failure(error, st, method) {
  const test=st && method ? `Test '${st.localClass.toUpperCase()}->${method.toUpperCase()}' in Main Program '${st.objectName.toUpperCase().padEnd(30,'=')}CP'` : null;
  const assertion=error?.osgAssertion;
  if (assertion || error?.constructor?.name?.toUpperCase()==='KERNEL_CX_ASSERT') {
    const a=assertion || {method:'ASSERT',msg:abapValue(error.msg),expected:abapValue(error.expected),actual:abapValue(error.actual)};
    const label=a.msg || `${method.replace(/(^|_)([a-z])/g,(_,p,c)=>p+c.toUpperCase())}: ${a.method}`;
    const detail=a.method==='ASSERT_EQUALS' ? (a.comparison || `Different values\nExpected [${a.expected}] Actual [${a.actual}]`) : (a.method==='ASSERT_FALSE'?'False expected':a.method==='ASSERT_TRUE'?'True expected':a.msg || abapValue(error.msg) || `Expected [${a.expected}] Actual [${a.actual}]`);
    return entry(`Critical Assertion Error: '${label}'`,'failedAssertion','critical',[detail,...(test?[test]:[])],'<Element stack>');
  }
  const name=error?.constructor?.name?.toUpperCase();
  if (name?.startsWith('CX_')) {
    let text=error.osgExceptionText || '';
    if (!text) try {text=abapValue(await error.if_message$get_text());} catch {}
    if (!text || /Dummy|^$/.test(text)) text=`${name}: ${abapValue(error.textid) || 'Exception raised'}`;
    return entry(`Exception Error <${name}>`,'exception','critical',[safeText(text),...(test?[test]:[])],'<Element stack>');
  }
  return runtimeEntry(error?.message || (typeof error==='string'?error:'ABAP runtime terminated'));
}
