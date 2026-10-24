// Deliberately small, strict tokenizer for the benchmark's flat Dictionary DDL.
const xml = s => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
const tag = (k,v) => `<${k}>${xml(v)}</${k}>`;
export function ddlToTable(source) {
  const tokens = [];
  for (let line of source.split(/\r?\n/)) {
    line = line.replace(/\/\/.*$/, '').trim();
    if (line) tokens.push(...(line.match(/@[\w.]+|'[^']*'|[A-Za-z_][\w.]*|\d+|[{}():;,]/g) || []));
  }
  let i = 0;
  const take = () => tokens[i++];
  const expect = s => {if (take()?.toLowerCase() !== s) throw Error(`DDL expected ${s} at token ${i}`);};
  expect('define'); expect('table');
  const name = take().toUpperCase(); expect('{');
  const fields = []; let currency;
  while (tokens[i] !== '}') {
    if (i >= tokens.length) throw Error('Unclosed DDL table');
    if (tokens[i].startsWith('@')) {
      expect('@semantics.amount.currencycode'); expect(':');
      currency = take().replaceAll("'", '').toUpperCase(); continue;
    }
    const key = tokens[i]?.toLowerCase() === 'key'; if (key) take();
    const field = take().toUpperCase(); expect(':');
    const type = take().toLowerCase(); let length, decimals = 0;
    if (tokens[i] === '(') {take(); length = Number(take()); if (tokens[i] === ',') {take(); decimals = Number(take());} expect(')');}
    const notnull = tokens[i]?.toLowerCase() === 'not'; if (notnull) {take(); expect('null');} expect(';');
    const types = {'abap.char':['CHAR','C',length,2*length], 'abap.numc':['NUMC','N',length,2*length], 'abap.int4':['INT4','X',10,4], 'abap.dats':['DATS','D',8,16], 'abap.cuky':['CUKY','C',5,10], 'abap.curr':['CURR','P',length,Math.ceil((length+1)/2)]};
    const t = types[type]; if (!t || !Number.isFinite(t[2])) throw Error(`Unsupported DDL type ${type}`);
    const data = {TABNAME:name,FIELDNAME:field,POSITION:fields.length+1,KEYFLAG:key?'X':'',ADMINFIELD:0,INTTYPE:t[1],INTLEN:t[3],DATATYPE:t[0],LENG:t[2],DECIMALS:decimals,NOTNULL:notnull?'X':''};
    if (currency) {const parts = currency.split('.');data.REFTABLE=parts.length>1?parts[0]:name;data.REFFIELD=parts.at(-1);currency=undefined;}
    fields.push('<DD03P>'+Object.entries(data).map(([k,v])=>tag(k,v)).join('')+'</DD03P>');
  }
  expect('}'); if (i !== tokens.length) throw Error('Trailing DDL tokens');
  return {name, filename:name.toLowerCase()+'.tabl.xml', contents:`<?xml version="1.0"?><abapGit version="v1.0.0" serializer="LCL_OBJECT_TABL" serializer_version="v1.0.0"><asx:abap xmlns:asx="http://www.sap.com/abapxml" version="1.0"><asx:values><DD02V>${tag('TABNAME',name)}<TABCLASS>TRANSP</TABCLASS><CLIDEP></CLIDEP><CONTFLAG>A</CONTFLAG><EXCLASS>1</EXCLASS></DD02V><DD03P_TABLE>${fields.join('')}</DD03P_TABLE></asx:values></asx:abap></abapGit>`};
}
