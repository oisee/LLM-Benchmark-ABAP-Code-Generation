" Expected failing stage: activation. See gaps-summary.json for observed diagnostics.
CLASS z_gap_infer DEFINITION PUBLIC.
  PUBLIC SECTION. CLASS-METHODS run.
ENDCLASS.
CLASS z_gap_infer IMPLEMENTATION.
  METHOD run.
    INSERT zsales_positions FROM TABLE @( VALUE #( ( zid = 1 zname = 'A' ) ) ).
  ENDMETHOD.
ENDCLASS.
