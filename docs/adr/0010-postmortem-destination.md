# 0010 — Where the post-mortem draft is published

Status: Proposed. Resolves architecture open item 10 and roadmap open point 3.

## Decision

As a comment on the incident's Jira ticket, linked from the Slack thread that
announced it. Confluence is deferred.

## Why

The draft belongs where the incident already lives. Jira is the one system that
already holds the diagnosis, the evidence links and the acceptance criterion; a
comment inherits the ticket's permissions, its notification list and its
searchability for free.

Confluence would mean a third write-capable integration, a page hierarchy and a
naming convention to agree, for a document whose first reader is the person
already looking at the ticket. If post-mortems later need to be a durable,
browsable corpus rather than an incident artefact, that is a real reason to add
Confluence — but it is a different requirement, and the roadmap does not yet make
it.

Slack gets a link, not the text: a post-mortem draft pasted into a channel is
unreadable and pushes the incident thread out of view.

## Revisit when

Post-mortems need to be found by people who were not on the incident. Search
across Jira comments is poor, and that is the point at which Confluence starts
paying for itself.
