package development.landmine.schemii.android.host;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Closed host entry: fixed actions only, no payloads, loaders or diagnostics. */
public final class HostActivity extends Activity {
    private TextView status;
    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        int pad = (int)(24 * getResources().getDisplayMetrics().density);
        layout.setPadding(pad,pad,pad,pad);
        status = new TextView(this);
        status.setText(HostService.status()); layout.addView(status);
        for (String action : new String[]{"start","stop","status"}) {
            Button button = new Button(this);
            button.setText(action.equals("start") ? "Check host prerequisites" : action.equals("stop") ? "Cancel check" : "Refresh status");
            button.setOnClickListener(view -> {
                if (!action.equals("status")) startForegroundService(new Intent(this,HostService.class).setAction(action));
                status.setText(HostService.status());
            });
            layout.addView(button);
        }
        setContentView(layout);
    }
    @Override public void onResume() { super.onResume(); if (status != null) status.setText(HostService.status()); }
}
