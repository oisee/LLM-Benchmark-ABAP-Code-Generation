import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {ddlToTable} from './ddl.mjs';
import {createEngine} from './engine.mjs';
import {root} from './bootstrap.mjs';

test('flat DDL preserves keys, currency references and client independence',()=>{
  const table=ddlToTable("define table zsale { key id : abap.int4 not null; curr : abap.cuky;\n@Semantics.amount.currencyCode : 'zsale.curr'\n amount : abap.curr(13,2); }");
  assert.match(table.contents,/<REFFIELD>CURR<\/REFFIELD>/);
  assert.match(table.contents,/<CLIDEP><\/CLIDEP>/);
  assert.match(table.contents,/<DECIMALS>2<\/DECIMALS>/);
  assert.throws(()=>ddlToTable('define table zbad { value : abap.unsupported; }'));
});

test('canonical, syntax/type mutants, assertion failure, and fresh answers', async()=>{
  const validate=await createEngine();
  const name='z_humaneval_000';
  const source=fs.readFileSync(path.resolve(root,`../../dataset/abap_canonical_solution/${name}.abap`),'utf8');
  const tests=fs.readFileSync(path.resolve(root,`../../dataset/abap_unittests/${name}_test.abap`),'utf8');
  const request={name,source,tests};
  assert.equal((await validate(request)).unit,true);
  assert.equal((await validate({...request,source:source.replace(/ENDMETHOD\./i,'invalid ??? . ENDMETHOD.')})).stage,'syntax');
  assert.equal((await validate({...request,source:source.replace(/ENDMETHOD\./i,'DATA bad TYPE z_osg_missing_type. ENDMETHOD.')})).stage,'syntax');
  assert.equal((await validate({...request,tests:tests.replace(/exp = abap_true/gi,'exp = abap_false')})).stage,'unit');
  assert.equal((await validate(request)).unit,true);
  const db={name:'z_osg_db',source:`CLASS z_osg_db DEFINITION PUBLIC FINAL CREATE PUBLIC.
    PUBLIC SECTION. CLASS-METHODS total RETURNING VALUE(result) TYPE i. ENDCLASS.
    CLASS z_osg_db IMPLEMENTATION. METHOD total.
      SELECT SUM( stock ) FROM zstock INTO @result.
    ENDMETHOD. ENDCLASS.`,tests:`CLASS ltc DEFINITION FOR TESTING DURATION SHORT RISK LEVEL HARMLESS.
    PRIVATE SECTION. METHODS test FOR TESTING. ENDCLASS.
    CLASS ltc IMPLEMENTATION. METHOD test.
      cl_abap_unit_assert=>assert_equals( act = z_osg_db=>total( ) exp = 0 ).
      DATA rows TYPE STANDARD TABLE OF zstock WITH DEFAULT KEY.
      rows = VALUE #( ( mat_id = 'A' stock = 2 ) ).
      INSERT zstock FROM TABLE @rows.
      cl_abap_unit_assert=>assert_equals( act = z_osg_db=>total( ) exp = 2 ).
      DELETE FROM zstock.
    ENDMETHOD. ENDCLASS.`};
  assert.equal((await validate(db)).unit,true);
  assert.equal((await validate(db)).unit,true);
  assert.equal((await validate({...db,source:db.source.replace('FROM zstock','FROM z_missing')})).stage,'syntax');
  assert.equal((await validate({...db,source:db.source.replace('SUM( stock )','SUM( missing_field )')})).stage,'unit');
});

test('each compat object fails without the adapter and preserves assertions', async()=>{
  const baseline=await createEngine({compat:false});
  const adapter=await createEngine();
  const source=`CLASS z_compat DEFINITION PUBLIC.
    PUBLIC SECTION. ENDCLASS. CLASS z_compat IMPLEMENTATION. ENDCLASS.`;
  const wrap=body=>`CLASS ltc DEFINITION FOR TESTING DURATION SHORT RISK LEVEL HARMLESS.
    PRIVATE SECTION. METHODS check FOR TESTING. ENDCLASS.
    CLASS ltc IMPLEMENTATION. METHOD check. ${body} ENDMETHOD. ENDCLASS.`;
  const bodies=[
    `DATA n TYPE i. DO 100 TIMES.
     CALL FUNCTION 'QF05_RANDOM_INTEGER' EXPORTING ran_int_min = -2 ran_int_max = 2 IMPORTING ran_int = n.
     cl_abap_unit_assert=>assert_equals( act = xsdbool( n >= -2 AND n <= 2 ) exp = abap_true ).
     ENDDO. cl_abap_unit_assert=>assert_equals( act = 2 exp = 2 ).`,
    `cl_aunit_assert=>assert_equals( act = 2 exp = 2 ).`,
    `DATA c TYPE char50. c = repeat( val = 'x' occ = 51 ).
     cl_abap_unit_assert=>assert_equals( act = strlen( c ) exp = 50 ).`,
    `DATA c TYPE char100. c = repeat( val = 'x' occ = 101 ).
     cl_abap_unit_assert=>assert_equals( act = strlen( c ) exp = 100 ).`,
    `DATA day TYPE c LENGTH 1.
     CALL FUNCTION 'DATE_COMPUTE_DAY' EXPORTING date = '20261019' IMPORTING day = day.
     cl_abap_unit_assert=>assert_equals( act = day exp = '1' ).
     CALL FUNCTION 'DATE_COMPUTE_DAY' EXPORTING date = '20261025' IMPORTING day = day.
     cl_abap_unit_assert=>assert_equals( act = day exp = '7' ).
     CALL FUNCTION 'DATE_COMPUTE_DAY' EXPORTING date = '20240229' IMPORTING day = day.
     cl_abap_unit_assert=>assert_equals( act = day exp = '4' ).
     CALL FUNCTION 'DATE_COMPUTE_DAY' EXPORTING date = '00010101' IMPORTING day = day.
     cl_abap_unit_assert=>assert_equals( act = day exp = '1' ).
     CALL FUNCTION 'DATE_COMPUTE_DAY' EXPORTING date = '19000301' IMPORTING day = day.
     cl_abap_unit_assert=>assert_equals( act = day exp = '4' ).
     CALL FUNCTION 'DATE_COMPUTE_DAY' EXPORTING date = '20000301' IMPORTING day = day.
     cl_abap_unit_assert=>assert_equals( act = day exp = '3' ).`
  ];
  for(const body of bodies) {
    const request={name:'z_compat',source,tests:wrap(body)};
    assert.equal((await baseline(request)).unit_syntax,false,body);
    const result=await adapter(request);
    assert.equal(result.unit,true,JSON.stringify(result));
    const mutant=await adapter({...request,tests:wrap(body.replace(/exp = (?:2|50|100|'1')/, 'exp = 999'))});
    assert.equal(mutant.stage,'unit',JSON.stringify(mutant));
  }
  for(const [low,high] of [[0,0],[2,1]]) {
    const result=await adapter({name:'z_compat',source,tests:wrap(`DATA n TYPE i.
      CALL FUNCTION 'QF05_RANDOM_INTEGER' EXPORTING ran_int_min = ${low} ran_int_max = ${high} IMPORTING ran_int = n.`)});
    assert.equal(result.stage,'unit','Undocumented random edge cases must fail closed');
  }
  const name='z_humaneval_007';
  const request={name,source:fs.readFileSync(path.resolve(root,`../../dataset/abap_canonical_solution/${name}.abap`),'utf8'),tests:fs.readFileSync(path.resolve(root,`../../dataset/abap_unittests/${name}_test.abap`),'utf8')};
  assert.equal((await baseline(request)).unit_syntax,false);
  assert.equal((await adapter(request)).unit,true);
});

test('SAP feedback reports each failing method without JS objects or paths',async()=>{
  const validate=await createEngine();
  const source=`CLASS z_feedback DEFINITION PUBLIC. PUBLIC SECTION. ENDCLASS.
    CLASS z_feedback IMPLEMENTATION. ENDCLASS.`;
  const tests=`CLASS ltc DEFINITION FOR TESTING DURATION SHORT RISK LEVEL HARMLESS.
    PRIVATE SECTION. METHODS test_2 FOR TESTING. METHODS test_1 FOR TESTING.
    METHODS test_3 FOR TESTING. METHODS test_4 FOR TESTING.
    ENDCLASS.
    CLASS ltc IMPLEMENTATION.
    METHOD test_2. cl_abap_unit_assert=>assert_equals( act = 7 exp = 8 msg = 'custom' ). ENDMETHOD.
    METHOD test_1. cl_abap_unit_assert=>assert_equals( act = abap_false exp = abap_true ). ENDMETHOD.
    METHOD test_3. DATA tab TYPE STANDARD TABLE OF i WITH DEFAULT KEY.
      DATA(value) = tab[ 4 ]. ENDMETHOD.
    METHOD test_4. DATA n TYPE i. n = 1 / 0. ENDMETHOD.
    ENDCLASS.`;
  const result=await validate({name:'z_feedback',source,tests});
  assert.equal(result.stage,'unit');
  assert.equal(result.unit_feedback.length,4);
  const [one,two,three,four]=result.unit_feedback;
  assert.deepEqual(Object.keys(one),['title','kind','severity','stack','details']);
  assert.equal(one.kind,'failedAssertion');
  assert.equal(one.details[0],'Different values\nExpected [X] Actual []');
  assert.match(one.title,/Test_1: ASSERT_EQUALS/);
  assert.equal(two.title,"Critical Assertion Error: 'custom'");
  assert.equal(two.details[0],'Different values\nExpected [8] Actual [7]');
  assert.equal(three.title,'Exception Error <CX_SY_ITAB_LINE_NOT_FOUND>');
  assert.equal(three.details[0],'A row with the index 4 is not in the table.');
  assert.equal(four.kind,'exception');
  assert.doesNotMatch(JSON.stringify(result.unit_feedback),/\.mjs|FRIENDS_ACCESS|\/home\/|EXTRA_CX|Error:.*\bat /);
  const syntax=await validate({name:'z_feedback',source:source.replace('PUBLIC SECTION.','PUBLIC SECTION. DATA bad TYPE z_missing_type.'),tests});
  assert.equal(syntax.stage,'syntax');
  assert.equal(typeof syntax.errors[0].line,'number');
  assert.equal(typeof syntax.errors[0].offset,'number');
  assert.doesNotMatch(syntax.errors[0].short_text,/unknown_types:|check_syntax:/);
});

test('ABAP display values, runtime abort and timeout shapes are stable', async()=>{
  const {abapValue,comparison,runtimeEntry,timeoutEntries,safeText}=await import('./feedback.mjs');
  assert.equal(abapValue({get:()=>({a:{get:()=> '0004'},b:{get:()=> 'X '}})}),'0004, X ');
  assert.equal(abapValue({array:()=>[{get:()=>1},{get:()=>2}]}),'1, 2');
  assert.deepEqual(runtimeEntry('bad\n    at file:///internal/run.mjs:4:2'),{
    title:'Failed due to a runtime abortion',kind:'runtimeAbortion',severity:'fatal',stack:[],details:['[bad]']});
  assert.equal(timeoutEntries(3).length,4);
  const {default:runtime}=await import('@abaplint/runtime');
  assert.equal(abapValue(new runtime.types.Integer().set(-2625)),'2.625-');
  const a=new runtime.types.Table(new runtime.types.Integer());
  const e=new runtime.types.Table(new runtime.types.Integer());
  e.append(new runtime.types.Integer().set(2));
  assert.equal(comparison(a,e),'Different values\nExpected [S-Table[1x4] of TYPE=I]\nActual [S-Table[0x4] of TYPE=I]\nActual table is empty');
  assert.deepEqual(timeoutEntries(1).map(e=>[e.kind,e.severity]),[['warning','tolerable'],['abortion','tolerable']]);
  assert.doesNotMatch(safeText('at /private/tool/engine.js:1:2'),/\/private/);
});


test('compiled cache changes for canonicalization, every DDL input and dependency lock', async()=>{
  const {compiledFingerprint}=await import('./engine.mjs');
  const tableDir=path.resolve(root,'../../dataset/abap_tables');
  const ddl=fs.readdirSync(tableDir).filter(f=>f.endsWith('.abap')).sort().map(f=>[f,fs.readFileSync(path.join(tableDir,f),'utf8')]);
  const lock=fs.readFileSync(path.join(root,'package-lock.json'),'utf8');
  const canonicalizer=fs.readFileSync(path.resolve(root,'../../src/abap_interaction.py'),'utf8');
  const baseline=compiledFingerprint([['library','source']],ddl,lock,canonicalizer);
  assert.notEqual(compiledFingerprint([['library','source']],ddl,lock,canonicalizer+'changed'),baseline);
  assert.equal(ddl.length,13);
  assert.notEqual(compiledFingerprint([['library','source']],ddl,lock+'changed'),baseline);
  for (let i=0;i<ddl.length;i++) {
    const mutant=structuredClone(ddl); mutant[i][1]+='changed';
    assert.notEqual(compiledFingerprint([['library','source']],mutant,lock),baseline,ddl[i][0]);
  }
});
