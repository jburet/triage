# 0028 — A class name is not a path, and the path Triage builds from one says so

Status: Proposed. `triage.errors.paths` and the suffix resolution in
`triage.analysis.context.gather` exist in code (M8 Phase 4); no analysis has read a real
repository from them, because no analysis has cloned a repository at all.

*Date: 2026-08-25*

## Decision

Datadog Error Tracking's `file_path` is a **fully-qualified class name** —
`zeenea.repository.orientdb.OdbClient.scala` — and its `function_name` a **synthetic JVM
symbol** — `$anonfun$load$6`. Neither is a path in a repository and neither is a name in the
source. F2 converts both by convention, points the analysis at the result *ahead of* the
selection profile's globs, and states the conversion in the report.

Three rules, in order:

1. **The package is the directory.** `a.b.C.scala` becomes `a/b/C.scala`, and the JVM's
   source root — `src/main/scala`, `src/main/java`, `src/main/kotlin` by suffix — is offered
   first.
2. **The tree decides which module.** A named path the tree does not carry outright is
   resolved by **suffix match**: `a/b/C.scala` finds
   `core/src/main/scala/a/b/C.scala` wherever the build put it. Two modules that both carry
   it are both read, up to three; picking one of them would be a guess and reading a dozen
   would be a selection rather than a location.
3. **A lambda names its method.** `$anonfun$load$6` and `lambda$load$3` become `load` — the
   name a developer can search for. The raw symbol is still printed beside it, because that
   is what Datadog said.

Anything that is already a path (it contains `/`) is passed through untouched and marked as
*not* derived. Anything that is neither — a bare `OdbClient.scala` — is looked for by name
and marked derived. An issue naming no file at all produces no paths and a reason.

Every derived path carries a caveat sentence into the report's exception header, saying what
was converted and that **which module holds it was not observed**.

## Why

**Because the alternative is measured, and it is the failure this fixes.** M7 3.3 ran a
`code_analysis` over the 4261-file Scala repository behind the 2026-08-24 incident. The
selection opened 47 files and **not one line of Scala** — twenty-six `build.sbt` files, the
READMEs, the workflows, the chart — and the model correctly returned `low`, which read like
judgement about the code rather than an empty selection. F2's whole cheapness is that the
issue *names the file*; handing that name over unconverted throws the advantage away, because
`AnalysisRequest.paths` is matched against tree paths and a class name matches none of them.

**Because the input shape is measured, not assumed.** On 2026-08-25, 202 of 202 Error
Tracking issues over a week named both a file and a function, and every one of them in this
form. This is not an edge case to handle defensively; it is the only case there is.

**Because the JVM convention is strong and the multi-module exception is exactly what suffix
matching covers.** `src/main/scala/<package>` is Maven's layout, sbt's default and Gradle's
default. What it does not tell you is which module — `core/`, `api/`, `indexer/` — and
that is the one thing the tree can answer for itself. So the convention gets the shape and
the tree gets the location, and neither is asked for what it does not know.

**Because a guess that is stated is not the guess ADR-0020 forbids.** ADR-0019 and ADR-0020
refuse a repository or a commit that nothing observed *presented as observed*. The same rule
applies here one axis over: a path built from a class name is Triage's construction, and the
report says so in the same breath as it says where the analysis looked. What is refused is a
silent conversion — a `*Files:*` line that reads identically whether Datadog handed over the
path or Triage manufactured it.

**Because reading two files is cheaper than choosing wrongly between them.** A package path
two modules both carry is genuinely ambiguous, and the budget for one analysis is sixty
files: spending two of them on both candidates costs nothing worth protecting, and picking
one silently would send a developer to the wrong module with no way to tell.

## Consequences

- `triage.analysis.context._named_first` now resolves a named path by suffix when the tree
  has no exact match. That is shared with M6's `iac_paths`, where the effect is the same and
  benign: a chart path that moved into a subdirectory is found rather than reported missing.
- The cap of three matches is arbitrary and untested against a real tree. A repository where
  a common class name matches three unrelated modules would spend three files of budget on
  it and report all three.
- The conversion is language-specific in exactly one place — the `SOURCE_ROOTS` table. A
  Python or Go service whose Error Tracking issues name a real path is unaffected, because
  the pass-through rule catches it first.
- The report's `*Files:*` line can therefore contain a path that **does not exist in the
  tree**. The gather already reports such a path back as a gap, so the analysis is not
  silently reading nothing; but the two are not yet joined up, and the report does not say
  which of the candidates was opened.

## Revisit when

An analysis actually clones a Zeenea repository and reads from these paths. Nothing has:
the investigative kinds have no image deployed (M7 3.4), so the whole chain from
`AnalysisRequest.paths` to an opened file is exercised only against fixtures. The first real
run is what says whether `src/main/scala/<package>` or the bare package path is the one the
tree carries — and if it is neither, this ADR is wrong in its first rule and the fix is the
table, not the design.

Revisit if Error Tracking starts returning repository-relative paths, which would happen if
the platform's OpenTelemetry agent were configured to emit `code.filepath`. Then rules 1 and
2 become dead code rather than wrong code, and the caveat should stop being printed.

Revisit the suffix cap if a report is ever seen naming three files where a developer expected
one. The number is a guess; the shape of the answer is not.
