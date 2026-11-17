package development.landmine.schemii.android.host;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Insets;
import android.os.Bundle;
import android.view.DisplayCutout;
import android.view.ViewGroup;
import android.view.Window;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/** Closed host entry: fixed actions only, no payloads, loaders or diagnostics. */
public final class HostActivity extends Activity {
    private TextView status;
    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        // Own the title inside the inset-aware content. Framework decoration
        // can overlap edge-to-edge content on modern targets.
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        int pad = (int)(24 * getResources().getDisplayMetrics().density);
        layout.setPadding(pad,pad,pad,pad);
        TextView title = new TextView(this);
        title.setText("Schemii host prerequisites");
        title.setTextSize(20);
        title.setAccessibilityHeading(true);
        layout.addView(title);
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
        scroll.addView(layout, new ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        scroll.setOnApplyWindowInsetsListener((view, insets) -> {
            // API29-compatible measurements, including a cutout that exceeds
            // the ordinary bar inset. Replace viewport padding on every
            // dispatch; the design padding belongs to the inner layout once.
            Insets bars = insets.getSystemWindowInsets();
            DisplayCutout cutout = insets.getDisplayCutout();
            view.setPadding(
                Math.max(bars.left, cutout == null ? 0 : cutout.getSafeInsetLeft()),
                Math.max(bars.top, cutout == null ? 0 : cutout.getSafeInsetTop()),
                Math.max(bars.right, cutout == null ? 0 : cutout.getSafeInsetRight()),
                Math.max(bars.bottom, cutout == null ? 0 : cutout.getSafeInsetBottom()));
            return insets;
        });
        setContentView(scroll);
        scroll.requestApplyInsets();
    }
    @Override public void onResume() { super.onResume(); if (status != null) status.setText(HostService.status()); }
}
