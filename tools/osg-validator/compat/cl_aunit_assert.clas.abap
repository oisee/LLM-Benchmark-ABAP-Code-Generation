" Clean-room compatibility for the only classic assertion used by the corpus.
CLASS cl_aunit_assert DEFINITION PUBLIC.
  PUBLIC SECTION.
    CLASS-METHODS assert_equals
      IMPORTING act TYPE any exp TYPE any
                msg TYPE csequence OPTIONAL tol TYPE f OPTIONAL
                quit TYPE i OPTIONAL level TYPE i OPTIONAL
      RETURNING VALUE(assertion_failed) TYPE abap_bool.
ENDCLASS.
CLASS cl_aunit_assert IMPLEMENTATION.
  METHOD assert_equals.
    assertion_failed = cl_abap_unit_assert=>assert_equals(
      act = act exp = exp msg = msg tol = tol quit = quit level = level ).
  ENDMETHOD.
ENDCLASS.
