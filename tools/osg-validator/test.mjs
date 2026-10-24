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
