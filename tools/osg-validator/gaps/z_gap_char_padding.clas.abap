" Expected failing stage: unit. See gaps-summary.json for observed diagnostics.
CLASS z_gap_char_padding DEFINITION PUBLIC.
  PUBLIC SECTION. CLASS-METHODS run.
ENDCLASS.
CLASS z_gap_char_padding IMPLEMENTATION.
  METHOD run.
    DELETE FROM zemp_master.
    DATA rows TYPE STANDARD TABLE OF zemp_master WITH DEFAULT KEY.
    rows = VALUE #( ( employee_id = 'A' name = 'Ada' ) ).
    INSERT zemp_master FROM TABLE @rows.
    DATA texts TYPE STANDARD TABLE OF string WITH DEFAULT KEY.
    SELECT name FROM zemp_master INTO TABLE @texts.
    DATA(text) = texts[ 1 ].
    cl_abap_unit_assert=>assert_equals( act = text exp = `Ada` ).
  ENDMETHOD.
ENDCLASS.
