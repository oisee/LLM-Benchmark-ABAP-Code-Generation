" Gregorian ordinal arithmetic, independent of the runtime date epoch.
" Contract: valid Gregorian DATS; Monday=1 through Sunday=7.
FUNCTION date_compute_day.
  DATA year TYPE i.
  DATA month TYPE i.
  DATA ordinal TYPE i.
  DATA prior_year TYPE i.
  year = date+0(4).
  month = date+4(2).
  prior_year = year - 1.
  ordinal = 365 * prior_year + prior_year DIV 4
          - prior_year DIV 100 + prior_year DIV 400.
  ordinal = ordinal + ( 367 * month - 362 ) DIV 12 + date+6(2).
  IF month > 2.
    IF year MOD 4 = 0 AND ( year MOD 100 <> 0 OR year MOD 400 = 0 ).
      ordinal = ordinal - 1.
    ELSE.
      ordinal = ordinal - 2.
    ENDIF.
  ENDIF.
  day = ( ordinal - 1 ) MOD 7 + 1.
ENDFUNCTION.
