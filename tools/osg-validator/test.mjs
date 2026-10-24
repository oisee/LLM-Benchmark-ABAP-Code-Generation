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
