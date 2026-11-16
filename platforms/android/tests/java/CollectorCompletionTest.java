package development.landmine.schemii.runtimeprobe;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/** Latches force cancellation while the actual production helper is in I/O. */
public final class CollectorCompletionTest {
    private static void require(boolean ok, String message) {
        if (!ok) throw new AssertionError(message);
    }
    private static void awaitIgnoringInterrupt(CountDownLatch latch) {
        boolean interrupted = false;
        for (;;) {
            try { require(latch.await(5, TimeUnit.SECONDS), "latch deadline"); break; }
            catch (InterruptedException error) { interrupted = true; }
        }
        if (interrupted) Thread.currentThread().interrupt();
    }
    private static void scenario(String stage, String cancellation) throws Exception {
        Object monitor = new Object();
        AtomicBoolean closing = new AtomicBoolean();
        AtomicBoolean retained = new AtomicBoolean(true);
        AtomicBoolean accepted = new AtomicBoolean();
        AtomicBoolean provisionalJournal = new AtomicBoolean();
        AtomicReference<CollectorCompletion> gate = new AtomicReference<>();
        AtomicReference<Throwable> failure = new AtomicReference<>();
        CountDownLatch entered = new CountDownLatch(1), resume = new CountDownLatch(1);
        Thread worker = new Thread(() -> {
            try {
                CollectorCompletion c = new CollectorCompletion(monitor, closing::get);
                gate.set(c);
                boolean committed = c.finish(() -> {
                    if (stage.equals("read")) { entered.countDown(); awaitIgnoringInterrupt(resume); }
                }, () -> {
                    provisionalJournal.set(true); // No accepted output/released ownership on disk.
                    if (stage.equals("journal")) { entered.countDown(); awaitIgnoringInterrupt(resume); }
                }, () -> {
                    require(retained.get(), "owner already discarded");
                    accepted.set(true); retained.set(false);
                });
                require(committed == cancellation.equals("none"), "wrong commit outcome");
                if (committed) { c.cancel(); require(!c.cancelled(), "post-commit cancellation rewrote completion"); }
            } catch (Throwable error) { failure.set(error); }
        });
        worker.start();
        if (!cancellation.equals("none")) {
            require(entered.await(5, TimeUnit.SECONDS), "I/O stage not reached");
            // Calling cancel synchronously while I/O is latched also proves that
            // cancellation does not wait for a lock held across that I/O.
            if (cancellation.equals("cancel")) gate.get().cancel();
            if (cancellation.equals("closing")) synchronized (monitor) { closing.set(true); gate.get().cancel(); }
            if (cancellation.equals("interrupt")) worker.interrupt();
            require(retained.get() && !accepted.get(), "premature owner release/output acceptance");
            resume.countDown();
        }
        worker.join(6000);
        require(!worker.isAlive(), "worker blocked");
        if (failure.get() != null) throw new AssertionError(failure.get());
        require(accepted.get() == cancellation.equals("none"), "output acceptance mismatch");
        require(retained.get() != cancellation.equals("none"), "ownership fence mismatch");
        if (stage.equals("journal") || cancellation.equals("none")) require(provisionalJournal.get(), "journal not prepared");
        System.out.println(stage + ":" + cancellation + ":PASS");
    }
    private static void ioFailure(String stage) throws Exception {
        AtomicBoolean retained = new AtomicBoolean(true), accepted = new AtomicBoolean();
        CollectorCompletion gate = new CollectorCompletion(new Object(), () -> false);
        boolean failed = false;
        try {
            gate.finish(() -> { if (stage.equals("read")) throw new java.io.IOException("read failure"); },
                () -> { throw new java.io.IOException("journal failure"); },
                () -> { accepted.set(true); retained.set(false); });
        } catch (java.io.IOException expected) { failed = true; }
        require(failed && retained.get() && !accepted.get(), "I/O failure discarded owner/output uncertainty");
        System.out.println(stage + ":io-failure:PASS");
    }
    public static void main(String[] args) throws Exception {
        scenario("success", "none");
        ioFailure("read"); ioFailure("journal");
        for (String stage : new String[]{"read", "journal"}) {
            for (String cancellation : new String[]{"cancel", "closing", "interrupt"}) scenario(stage, cancellation);
        }
    }
}
