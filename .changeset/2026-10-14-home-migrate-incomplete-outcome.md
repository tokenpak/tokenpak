---
"tokenpak": patch
---

`tokenpak home migrate --apply` no longer reports a migration as done when items
were left unresolved. A database whose schema differs from the one in `~/.tpk`
is not merged and nothing is copied from it, yet the run used to print "Done",
exit 0, write a finished receipt and let `tokenpak doctor` stop warning about the
split home, which invited removing the only copy of that history. An apply that
leaves any conflict now lists the items, says which ones have no copy in
`~/.tpk` and which have a `.legacy` file beside the `~/.tpk` one, tells you to
keep `~/.tokenpak`, and exits 1. The JSON report keeps its fields and adds
`outcome`, `unresolved` and `receipt`. A dry run still changes nothing and exits 0.

The receipt records `status` and the unresolved items. `tokenpak doctor` warns
about an incomplete migration, including receipts from earlier releases whose
summary shows conflicts, and an earlier receipt is removed before an apply starts,
so a run that stops part-way cannot leave an old success standing. If the receipt
cannot be written the run says so and exits 1 instead of claiming it finished.
The incomplete-migration warning does not depend on which files `tokenpak doctor`
treats as home state, so a home holding only a ledger such as `cost.db` is covered,
and the general "both homes exist" warning no longer tells you to remove
`~/.tokenpak`; it says to keep it until a migration has finished with nothing
unresolved. A clean apply is unchanged and can be repeated. No public symbol is removed or
renamed.
