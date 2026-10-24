" Expected failing stage: unit. See gaps-summary.json for observed diagnostics.
CLASS z_gap_do_count DEFINITION PUBLIC.
  PUBLIC SECTION. CLASS-METHODS run.
ENDCLASS.
CLASS z_gap_do_count IMPLEMENTATION.
  METHOD run.
    DATA count TYPE i. DATA length TYPE i VALUE 8.
    DO length / 2 TIMES. count = count + 1. ENDDO.
    cl_abap_unit_assert=>assert_equals( act = count exp = 4 ).
  ENDMETHOD.
ENDCLASS.
