package development.landmine.schemii.runtimeprobe;

import java.io.IOException;
import org.json.JSONArray;
import org.json.JSONObject;

/** Admission of one exact finite diagnostic report, never generic containment. */
final class IntegrationChecks {
    private static void require(boolean ok, String message) throws IOException {
        if (!ok) throw new IOException(message);
    }
    static JSONArray releasedBirths(JSONObject report, long uid, String boot) throws Exception {
        JSONObject c = report.getJSONArray("cases").getJSONObject(0);
        JSONArray identities = new JSONArray();
        for (String name : new String[]{"expected", "changed"}) {
            JSONArray records = c.getJSONArray(name);
            for (int i = 0; i < records.length(); ++i) identities.put(records.getJSONObject(i).getJSONObject("identity"));
        }
        JSONArray found = c.getJSONArray("discovered");
        for (int i = 0; i < found.length(); ++i) identities.put(found.getJSONObject(i));
        identities.put(c.getJSONObject("owner")).put(c.getJSONObject("orphan_after_owner_loss"));
        java.util.Set<String> seen = new java.util.HashSet<>();
        JSONArray observations = new JSONArray();
        for (int i = 0; i < identities.length(); ++i) {
            JSONObject id = identities.getJSONObject(i);
            long pid = id.getLong("pid"), birth = id.getLong("start_ticks");
            if (pid == 0 && birth == 0) continue; // Unobserved/unlaunched identity, never authority.
            require(pid > 0 && birth > 0 && id.getString("boot_id").equals(boot) && id.getLong("kernel_uid") == uid, "Finite post-check birth/boot/UID unknown");
            if (!seen.add(pid + ":" + birth)) continue;
            JSONObject observed = new JSONObject().put("pid", pid).put("start_ticks", birth).put("boot_id", boot);
            try {
                // Read only these exact source-bound fixture births. Never
                // adopt, signal or scan other PIDs, groups, names or UIDs.
                String stat = new String(java.nio.file.Files.readAllBytes(java.nio.file.Paths.get("/proc/" + pid + "/stat")), "UTF-8");
                String[] fields = stat.substring(stat.lastIndexOf(')') + 2).trim().split("\\s+");
                long currentBirth = Long.parseLong(fields[19]);
                observed.put("current_start_ticks", currentBirth).put("state", fields[0]);
                require(currentBirth != birth, "Known finite birth still present after oracle cleanup; retain owner/fence");
                observed.put("observation", "different-birth");
            } catch (java.nio.file.NoSuchFileException absent) {
                observed.put("observation", "absent");
            }
            // EACCES, malformed stat and every other unknown escape; the
            // collector retains the captured Process and original report.
            observations.put(observed);
        }
        require(observations.length() > 0, "No finite post-cleanup birth witnesses");
        return observations;
    }

    static void subreaper(JSONObject report, String source, String which, int exit,
                          String architecture, long uid, String boot) throws Exception {
        require(report.getString("probe").equals("subreaper-orphan-control")
            && report.getString("source_id").equals(source), "Subreaper runtime source mismatch");
        require(report.getString("compiled_platform").equals("android") && report.getInt("compiled_api") == 29
            && report.getString("compiled_arch").equals(architecture), "Subreaper native ABI/API mismatch");
        require(!report.getBoolean("runtime_acceptance") && !report.getBoolean("production_ownership")
            && !report.getBoolean("owner_death_solution"), "Diagnostic promoted to production");
        require(report.getBoolean("kernel_uid_known") && report.getLong("kernel_uid") == uid
            && report.getBoolean("oracle_subreaper") && report.getInt("harness_errno") == 0,
            "Subreaper prerequisite unknown/refused; retain state and stop");
        JSONArray cases = report.getJSONArray("cases");
        require(cases.length() == 1 && cases.getJSONObject(0).getString("name").equals(which), "Subreaper case inventory mismatch");
        JSONObject c = cases.getJSONObject(0);
        // cleanup_errno==0 is the reviewed outer oracle's specific finite-tree
        // release/reaping through ECHILD. It is NOT the candidate's cleanup.
        require(c.getInt("cleanup_errno") == 0, "Finite oracle cleanup unproved; retain captured owner and fence");
        boolean complete = c.getBoolean("candidate_complete"), passed = c.getBoolean("control_pass");
        require(report.getBoolean("controls_pass") == passed
            && report.getBoolean("selected_candidate_cases_complete") == complete
            && exit == (complete && passed ? 0 : 1), "Subreaper exit/summary contradiction");
        boolean positive = which.equals("fast-orphan") || which.equals("nested-orphan") || which.equals("new-session") || which.equals("new-group") || which.equals("sigint");
        require(!complete || positive, "Refusal/owner-loss case claims completeness");
        require(!passed || !positive || complete, "Positive control hides incomplete candidate");
        if (complete) {
            require(passed && c.getInt("error") == 0 && c.getInt("candidate_errno") == 0
                && c.getBoolean("candidate_waited") && c.getInt("candidate_exit_code") == 0
                && c.getInt("candidate_exit_signal") == 0 && c.getBoolean("no_children")
                && c.getBoolean("peer_survived") && !c.getBoolean("oracle_intervention")
                && c.getBoolean("owner_verified_inside") && c.getBoolean("owner_verified_outside")
                && c.getBoolean("intermediates_reaped_before_first_scan") && c.getBoolean("waitability_verified")
                && c.getBoolean("scan_complete") && c.getInt("scan_errno") == 0 && c.getInt("scan_unknown") == 0
                && c.getInt("subreaper_before") == 0 && c.getInt("subreaper_after") == 1
                && c.getInt("prctl_errno") == 0, "Candidate completeness contradicts raw witnesses");
            int count = which.equals("nested-orphan") ? 3 : 2;
            require(c.getInt("sent") == count && c.getInt("reaped") == count, "Finite signal/reap counts mismatch");
            JSONObject owner = c.getJSONObject("owner");
            require(owner.getLong("start_ticks") > 0 && owner.getString("boot_id").equals(boot)
                && owner.getLong("kernel_uid") == uid, "Owner birth mismatch");
            JSONArray found = c.getJSONArray("discovered"), expected = c.getJSONArray("expected");
            require(found.length() == count, "Candidate finite inventory size mismatch");
            for (int role = 0; role < count; ++role) {
                JSONObject witness = null;
                for (int j = 0; j < expected.length(); ++j) if (expected.getJSONObject(j).getInt("role") == role) witness = expected.getJSONObject(j).getJSONObject("identity");
                require(witness != null, "Independent finite witness missing");
                int matches = 0;
                for (int j = 0; j < found.length(); ++j) {
                    JSONObject id = found.getJSONObject(j);
                    if (id.getLong("pid") == witness.getLong("pid") && id.getLong("start_ticks") == witness.getLong("start_ticks")
                        && id.getString("boot_id").equals(boot) && id.getLong("kernel_uid") == uid
                        && id.getLong("ppid") == owner.getLong("pid")) ++matches;
                }
                require(matches == 1 && witness.getLong("start_ticks") > 0, "Finite reparented birth set mismatch");
                require(c.getJSONArray("acknowledged_signals").getInt(role) == (which.equals("sigint") ? 2 : 15), "Finite signal acknowledgement mismatch");
            }
        }
        if (which.equals("owner-loss") && passed) {
            require(!complete && c.getBoolean("owner_lost") && c.getBoolean("orphan_survived_owner_loss")
                && c.getBoolean("oracle_intervention") && c.getBoolean("candidate_waited")
                && c.getInt("candidate_exit_code") == -1 && c.getInt("candidate_exit_signal") == 15,
                "Owner-loss intervention hidden or contradicted");
        }
    }
}
