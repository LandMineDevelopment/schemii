"""Execute the production Activity against bounded framework layout doubles.

These controls cover inset ownership and action dispatch, not Android rendering.
The actual target36 window still requires independent PNG/XML acceptance.
"""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
ACTIVITY = ROOT / (
    "platforms/android/app/src/main/java/"
    "development/landmine/schemii/android/host/HostActivity.java"
)
PACKAGE = "development.landmine.schemii.android.host"

STUBS = {
    "android/os/Bundle.java": "package android.os; public class Bundle {}",
    "android/content/Context.java": "package android.content; public class Context {}",
    "android/content/pm/ApplicationInfo.java": """
package android.content.pm;
public class ApplicationInfo { public String nativeLibraryDir="/fixture/framework-library"; }
""",
    "android/content/Intent.java": """
package android.content;
public class Intent {
    public String action;
    public Intent(Context context, Class<?> type) {}
    public Intent setAction(String value) { action=value; return this; }
}
""",
    "android/content/res/Resources.java": """
package android.content.res;
public class Resources {
    public static float density=1.5f;
    public static class Metrics { public float density=Resources.density; }
    public Metrics getDisplayMetrics() { return new Metrics(); }
}
""",
    "android/graphics/Insets.java": """
package android.graphics;
public final class Insets {
    public final int left,top,right,bottom;
    public Insets(int l,int t,int r,int b) { left=l;top=t;right=r;bottom=b; }
}
""",
    "android/view/DisplayCutout.java": """
package android.view;
public final class DisplayCutout {
    private final int l,t,r,b;
    public DisplayCutout(int l,int t,int r,int b) { this.l=l;this.t=t;this.r=r;this.b=b; }
    public int getSafeInsetLeft() { return l; }
    public int getSafeInsetTop() { return t; }
    public int getSafeInsetRight() { return r; }
    public int getSafeInsetBottom() { return b; }
}
""",
    "android/view/WindowInsets.java": """
package android.view;
import android.graphics.Insets;
public final class WindowInsets {
    private final Insets system;
    private final DisplayCutout cutout;
    public WindowInsets(Insets s,DisplayCutout c) { system=s;cutout=c; }
    public Insets getSystemWindowInsets() { return system; }
    public DisplayCutout getDisplayCutout() { return cutout; }
}
""",
    "android/view/Window.java": """
package android.view;
public final class Window { public static final int FEATURE_NO_TITLE=1; }
""",
    "android/view/View.java": """
package android.view;
public class View {
    public interface OnClickListener { void onClick(View view); }
    public interface OnApplyWindowInsetsListener {
        WindowInsets onApplyWindowInsets(View view,WindowInsets insets);
    }
    public int left,top,right,bottom;
    public boolean requestedInsets, heading;
    private OnClickListener click;
    private OnApplyWindowInsetsListener insets;
    public void setPadding(int l,int t,int r,int b) { left=l;top=t;right=r;bottom=b; }
    public void setOnClickListener(OnClickListener listener) { click=listener; }
    public void performClick() { if(click!=null)click.onClick(this); }
    public void setOnApplyWindowInsetsListener(OnApplyWindowInsetsListener l) { insets=l; }
    public WindowInsets dispatchInsets(WindowInsets value) {
        return insets==null?value:insets.onApplyWindowInsets(this,value);
    }
    public void requestApplyInsets() { requestedInsets=true; }
    public void setAccessibilityHeading(boolean value) { heading=value; }
}
""",
    "android/view/ViewGroup.java": """
package android.view;
import java.util.*;
public class ViewGroup extends View {
    public static class LayoutParams {
        public static final int MATCH_PARENT=-1, WRAP_CONTENT=-2;
        public final int width,height;
        public LayoutParams(int w,int h) { width=w;height=h; }
    }
    public final List<View> children=new ArrayList<>();
    public LayoutParams childParams;
    public void addView(View child) { children.add(child); }
    public void addView(View child,LayoutParams p) { addView(child);childParams=p; }
}
""",
    "android/widget/LinearLayout.java": """
package android.widget;
import android.content.Context;
import android.view.ViewGroup;
public class LinearLayout extends ViewGroup {
    public static final int VERTICAL=1;
    public int orientation;
    public LinearLayout(Context context) {}
    public void setOrientation(int value) { orientation=value; }
}
""",
    "android/widget/ScrollView.java": """
package android.widget;
import android.content.Context;
import android.view.ViewGroup;
public class ScrollView extends ViewGroup {
    public boolean fill;
    public ScrollView(Context context) {}
    public void setFillViewport(boolean value) { fill=value; }
}
""",
    "android/widget/TextView.java": """
package android.widget;
import android.content.Context;
import android.view.View;
public class TextView extends View {
    public String text="";
    public float size;
    public TextView(Context context) {}
    public void setText(CharSequence value) { text=value.toString(); }
    public void setTextSize(float value) { size=value; }
}
""",
    "android/widget/Button.java": """
package android.widget;
import android.content.Context;
public class Button extends TextView { public Button(Context context) { super(context); } }
""",
    "android/app/Activity.java": """
package android.app;
import android.content.*;
import android.content.res.Resources;
import android.os.Bundle;
import android.view.View;
import java.util.*;
public class Activity extends Context {
    public View content;
    public boolean noTitle;
    public final List<String> actions=new ArrayList<>();
    protected void onCreate(Bundle saved) {}
    protected void onResume() {}
    public boolean requestWindowFeature(int feature) {
        if(content!=null)throw new AssertionError("late decoration request");
        noTitle=feature==1;return true;
    }
    public Resources getResources() { return new Resources(); }
    public android.content.pm.ApplicationInfo getApplicationInfo() {
        return new android.content.pm.ApplicationInfo();
    }
    public void setContentView(View value) { content=value; }
    public void startForegroundService(Intent intent) { actions.add(intent.action); }
}
""",
    "development/landmine/schemii/android/host/HostService.java": """
package development.landmine.schemii.android.host;
public class HostService {
    public static String value="initial refusal";
    public static String status() { return value; }
}
""",
    "development/landmine/schemii/android/host/NativeOwner.java": """
package development.landmine.schemii.android.host;
public class NativeOwner {
    static final class OwnMapping {
        static String value="Own mapping not checked";
        static int starts;
        static void start(String directory) {
            if (!directory.equals("/fixture/framework-library")) throw new AssertionError("framework reference");
            ++starts;
            value="Own mapping pending";
        }
        static String status() { return value; }
    }
}
""",
}

HARNESS = """
package development.landmine.schemii.android.host;
import android.graphics.Insets;
import android.view.*;
import android.widget.*;
import android.content.res.Resources;
public class ActivityHarness {
    static void require(boolean yes,String reason) {
        if(!yes)throw new AssertionError(reason);
    }
    static HostActivity create() {
        HostActivity a=new HostActivity();a.onCreate(null);return a;
    }
    static ScrollView scroll(HostActivity a) {
        require(a.noTitle,"framework title must not cover content");
        require(a.content instanceof ScrollView,"overflow requires scrolling");
        ScrollView s=(ScrollView)a.content;
        require(s.fill && s.requestedInsets,"request measured insets on scroll viewport");
        require(s.childParams.height==ViewGroup.LayoutParams.WRAP_CONTENT,"content can grow");
        return s;
    }
    static LinearLayout body(ScrollView s) { return (LinearLayout)s.children.get(0); }
    static void padding(View v,int l,int t,int r,int b) {
        require(v.left==l&&v.top==t&&v.right==r&&v.bottom==b,"wrong inset/baseline padding");
    }
    public static void main(String[] args) {
        HostActivity a=create();ScrollView s=scroll(a);LinearLayout layout=body(s);
        require(layout.orientation==LinearLayout.VERTICAL,"vertical readable flow");
        padding(layout,36,36,36,36);
        TextView title=(TextView)layout.children.get(0);
        require(title.text.equals("Schemii host prerequisites")&&title.heading,"content owns accessible title");
        if(args[0].equals("insets")) {
            WindowInsets i=new WindowInsets(new Insets(0,48,0,24),new DisplayCutout(8,60,9,0));
            require(s.dispatchInsets(i)==i,"preserve downstream insets");padding(s,8,60,9,24);
            s.dispatchInsets(i);padding(s,8,60,9,24);padding(layout,36,36,36,36);
        } else if(args[0].equals("rotation")) {
            s.dispatchInsets(new WindowInsets(new Insets(0,48,0,24),null));
            s.dispatchInsets(new WindowInsets(new Insets(44,0,44,12),null));
            padding(s,44,0,44,12);padding(layout,36,36,36,36);
            Resources.density=2f;ScrollView next=scroll(create());padding(body(next),48,48,48,48);
        } else if(args[0].equals("mapping")) {
            require(layout.children.size()==7,"one separate action and status");
            Button mapping=(Button)layout.children.get(5);
            TextView evidence=(TextView)layout.children.get(6);
            require(mapping.text.equals("Check own mapping") && evidence.text.equals("Own mapping not checked"),
                "explicit mapping action inside the inset-aware layout");
            a.onResume(); ((Button)layout.children.get(4)).performClick();
            require(NativeOwner.OwnMapping.starts==0 && a.actions.isEmpty(),"refresh and resume do not start work");
            mapping.performClick();
            require(evidence.text.equals("Own mapping pending") && a.actions.isEmpty()
                && NativeOwner.OwnMapping.starts==1,"tap updates only mapping state");
            require(((TextView)layout.children.get(1)).text.equals("initial refusal"),"separate host status");
            HostActivity next=create();
            LinearLayout recreated=body(scroll(next));
            TextView nextEvidence=(TextView)recreated.children.get(6);
            require(nextEvidence.text.equals("Own mapping pending") && NativeOwner.OwnMapping.starts==1,
                "recreation reads pending without starting another check");
            NativeOwner.OwnMapping.value="Own mapping matched";
            ((Button)recreated.children.get(4)).performClick();
            require(nextEvidence.text.equals("Own mapping matched") && next.actions.isEmpty(),
                "existing refresh reads terminal mapping state without service intent");
            next.onResume();
            require(nextEvidence.text.equals("Own mapping matched") && NativeOwner.OwnMapping.starts==1,
                "resume preserves terminal status");
        } else {
            TextView status=(TextView)layout.children.get(1);
            require(status.text.equals("initial refusal"),"initial status preserved");
            String[] labels={"Check host prerequisites","Cancel check","Refresh status"};
            for(int i=0;i<3;i++)require(((Button)layout.children.get(i+2)).text.equals(labels[i]),"fixed actions");
            ((Button)layout.children.get(2)).performClick();
            ((Button)layout.children.get(3)).performClick();
            require(a.actions.size()==2&&a.actions.get(0).equals("start")&&a.actions.get(1).equals("stop"),"service actions unchanged");
            HostService.value="retained error "+"x".repeat(4000);
            ((Button)layout.children.get(4)).performClick();
            require(status.text.equals(HostService.value)&&a.actions.size()==2,"refresh is observation only");
            HostService.value="resume error";a.onResume();require(status.text.equals("resume error"),"resume refresh");
        }
    }
}
"""


@unittest.skipUnless(shutil.which("javac") and shutil.which("java"), "JDK required")
class HostActivityLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="schemii-activity-layout-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        files = []
        sources = dict(STUBS)
        sources[PACKAGE.replace(".", "/") + "/HostActivity.java"] = ACTIVITY.read_text()
        sources[PACKAGE.replace(".", "/") + "/ActivityHarness.java"] = HARNESS
        for name, value in sources.items():
            target = cls.folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value)
            files.append(str(target))
        subprocess.run(
            ["javac", "-d", str(cls.folder), *files], check=True, capture_output=True
        )

    def run_case(self, mode):
        result = subprocess.run(
            ["java", "-cp", str(self.folder), PACKAGE + ".ActivityHarness", mode],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_system_bars_cutouts_and_repeat_dispatch(self):
        self.run_case("insets")

    def test_rotated_insets_and_density_recreation(self):
        self.run_case("rotation")

    def test_fixed_actions_long_status_and_resume(self):
        self.run_case("dispatch")

    def test_own_mapping_action_and_recreated_status(self):
        self.run_case("mapping")
