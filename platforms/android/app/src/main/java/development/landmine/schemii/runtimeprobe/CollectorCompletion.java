package development.landmine.schemii.runtimeprobe;

import java.util.function.BooleanSupplier;

/** Linearizes cancellation and ownership release; all file I/O precedes commit. */
final class CollectorCompletion {
    interface Work { void run() throws Exception; }
    private final Object monitor;
    private final BooleanSupplier closing;
    private final Thread collector;
    private boolean cancelled;
    private boolean committed;

    CollectorCompletion(Object monitor, BooleanSupplier closing) {
        this.monitor = monitor;
        this.closing = closing;
        this.collector = Thread.currentThread();
    }

    void cancel() {
        synchronized (monitor) {
            if (!committed) cancelled = true;
        }
    }

    private boolean cancelledLocked() {
        if (!committed && (closing.getAsBoolean() || collector.isInterrupted())) cancelled = true;
        return cancelled;
    }

    boolean cancelled() {
        synchronized (monitor) { return cancelledLocked(); }
    }

    boolean finish(Work read, Work prepareJournal, Work commitInMemory) throws Exception {
        if (!cancelled()) read.run();
        // The durable record remains explicitly provisional, with no accepted
        // output or released ownership. Cancellation never waits for file I/O.
        if (!cancelled()) prepareJournal.run();
        synchronized (monitor) {
            if (cancelledLocked()) return false;
            // Only in-memory result acceptance + removal of the captured owner.
            // No file I/O, JSON parsing or serialization is permitted here.
            commitInMemory.run();
            committed = true;
            return true;
        }
    }
}
