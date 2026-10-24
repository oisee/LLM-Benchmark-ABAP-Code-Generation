" Expected failing stage: unit. See gaps-summary.json for observed diagnostics.
CLASS z_gap_aggregate DEFINITION PUBLIC.
  PUBLIC SECTION. CLASS-METHODS run.
ENDCLASS.
CLASS z_gap_aggregate IMPLEMENTATION.
  METHOD run.
    DATA total TYPE i. SELECT SUM( osg_missing_field ) FROM zstock INTO @total.
  ENDMETHOD.
ENDCLASS.
