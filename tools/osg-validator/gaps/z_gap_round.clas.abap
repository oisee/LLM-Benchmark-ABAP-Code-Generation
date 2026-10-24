" Expected failing stage: unit. See gaps-summary.json for observed diagnostics.
CLASS z_gap_round DEFINITION PUBLIC.
  PUBLIC SECTION. CLASS-METHODS run.
ENDCLASS.
CLASS z_gap_round IMPLEMENTATION.
  METHOD run.
    DATA value TYPE f. value = round( val = CONV f( '1.234' ) dec = 2 ).
    cl_abap_unit_assert=>assert_equals( act = value exp = CONV f( '1.23' ) ).
  ENDMETHOD.
ENDCLASS.
