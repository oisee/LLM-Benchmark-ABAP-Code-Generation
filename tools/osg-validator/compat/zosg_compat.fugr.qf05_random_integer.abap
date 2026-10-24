" Supported documented domain: RAN_INT_MIN < RAN_INT_MAX, inclusive integers.
" Fail closed outside that domain; do not guess SAP singleton/error semantics.
FUNCTION qf05_random_integer.
  ASSERT ran_int_min < ran_int_max.
  WRITE '@KERNEL const {randomInt} = await import("node:crypto");'.
  WRITE '@KERNEL ran_int.set(randomInt(ran_int_min.get(), ran_int_max.get() + 1));'.
ENDFUNCTION.
