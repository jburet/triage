"""Which repository a running service is, and where its infrastructure is defined.

F0's map is keyed on the name a repository says it deploys as, which is right
for the multi-tenant applications and wrong for the mono-tenant one: what alerts
is ``plt-merck-qa``, a customer, and no repository claims it. This package
replaces the hand-maintained guess with a derivation — the seed document for
what no cluster knows, the running workload's own image for the rest.
"""
