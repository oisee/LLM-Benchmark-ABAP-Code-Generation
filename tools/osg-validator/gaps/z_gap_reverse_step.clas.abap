" Expected failing stage: unit. See gaps-summary.json for observed diagnostics.
CLASS z_gap_reverse_step DEFINITION PUBLIC.
  PUBLIC SECTION. CLASS-METHODS run.
ENDCLASS.
CLASS z_gap_reverse_step IMPLEMENTATION.
  METHOD run.
    DATA rows TYPE int4_table. rows = VALUE #( ( 1 ) ( 2 ) ( 3 ) ).
    DATA reversed TYPE int4_table.
    LOOP AT rows INTO DATA(row) FROM lines( rows ) TO 1 STEP -1. APPEND row TO reversed. ENDLOOP.
    cl_abap_unit_assert=>assert_equals( act = reversed exp = VALUE int4_table( ( 3 ) ( 2 ) ( 1 ) ) ).
  ENDMETHOD.
ENDCLASS.
