# Schemer report and drill freshness

A dashboard refresh queries current source data in one request. Drilling into a
group starts a separate request against current source data. A drill is an
investigation of that group now; changed source rows can disagree with the earlier
report total. Schemer does not retain the database snapshot behind the report.

The expanded report and drill show their own server-recorded **request started**
times, including the local time zone. The wire field is named `snapshotAt`, but
the server records it before execution admission. It is a request timestamp,
not an exact database snapshot acquisition time or a historical as-of guarantee.
While a request is starting, the viewer waits for that metadata; an unavailable
time is not replaced with the browser's clock.

Completed drill previews are bounded browser results. Reopening one issues no new
query, retains its original request time, and labels it as an earlier cached
result. **Refresh detail rows** disposes that selection's old preview and queries
current data again, without refreshing or cancelling the report. Closing or
switching away from an unfinished drill, including returning to the report pane,
stops its request; the main dashboard's
eager stream keeps running. Cache eviction and browser row/byte limits remain in
force, including after Refresh. If no browser budget is available, no new source
query starts.

**Export cached rows as CSV** downloads the displayed bounded preview without a
new query. **Download full results** runs a separate query against a fresh source
snapshot with current dashboard selections and revision. The viewer discloses
this beside the download control, and the response carries
`X-Schemer-Snapshot: fresh`; full-download rows can differ from both previews.
