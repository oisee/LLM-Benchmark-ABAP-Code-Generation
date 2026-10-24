" Expected failing stage: activation. See gaps-summary.json for observed diagnostics.
CLASS z_gap_sql_upper DEFINITION PUBLIC.
  PUBLIC SECTION. CLASS-METHODS run.
ENDCLASS.
CLASS z_gap_sql_upper IMPLEMENTATION.
  METHOD run.
    DATA rows TYPE STANDARD TABLE OF zcountry_codes WITH DEFAULT KEY.
    SELECT * FROM zcountry_codes INTO TABLE @rows WHERE upper( country_name ) = 'GERMANY'.
  ENDMETHOD.
ENDCLASS.
