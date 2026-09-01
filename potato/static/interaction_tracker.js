/**
 * Interaction Tracker - Captures user interactions for behavioral analysis
 *
 * This module tracks user interactions with the annotation interface including:
 * - Clicks on annotation elements
 * - Focus changes between elements
 * - Scroll depth
 * - Keyboard shortcuts
 * - Navigation events
 * - AI assistance usage
 * - Annotation changes
 *
 * Events are batched and sent periodically to minimize network overhead.
 * Uses sendBeacon API for reliable delivery on page unload.
 */
class InteractionTracker {
    constructor() {
        this.events = [];
        this.focusStartTime = {};
        this.focusTime = {};
        this.scrollDepthMax = 0;
        this.currentInstanceId = null;
        this.previousInstanceId = null;
        this.flushInterval = 5000; // Flush every 5 seconds
        this.lastFlush = Date.now();
        this.isInitialized = false;
        this.debugMode = false;

        // Don't auto-init - wait for explicit init call or DOMContentLoaded
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => this.init());
        } else {
            this.init();
        }
    }

    init() {
        if (this.isInitialized) return;
        this.isInitialized = true;

        // Track clicks on annotation elements
        document.addEventListener('click', (e) => this.trackClick(e), true);

        // Track focus changes
        //document.addEventListener('focusin', (e) => this.trackFocusIn(e), true);
        //document.addEventListener('focusout', (e) => this.trackFocusOut(e), true);

        // Track page visibility changes
        document.addEventListener("visibilitychange", (e) => this.trackVisibilityChange(e), true);

        // Track scroll depth
        window.addEventListener('scroll', () => this.trackScroll(), { passive: true });

        // Track keyboard shortcuts
        document.addEventListener('keydown', (e) => this.trackKeypress(e), true);

        // Flush on page unload
        window.addEventListener('beforeunload', () => this.flush(true));
        window.addEventListener('pagehide', () => this.flush(true));

        initCopyTracking();

        // Periodic flush
        this.flushTimer = setInterval(() => this.flush(false), this.flushInterval);

        if (this.debugMode) {
            console.log('[InteractionTracker] Initialized');
        }
    }

    /**
     * Set the current instance ID and notify about navigation
     * @param {string} instanceId - The new instance ID
     */
    setInstanceId(instanceId) {
        if (this.debugMode) {
            console.log(`[InteractionTracker] setInstanceId: ${instanceId}`);
        }

        // Flush events for previous instance
        if (this.currentInstanceId && this.currentInstanceId !== instanceId) {
            this.flush(true);
        }

        this.previousInstanceId = this.currentInstanceId;
        this.currentInstanceId = instanceId;

        // Reset scroll depth for new instance
        this.scrollDepthMax = 0;

        this.addEvent('navigation', 'instance_load', {
            instance_id: instanceId,
            from_instance: this.previousInstanceId
        });
    }

    initCopyTracking() {
        ['copy', 'cut'].forEach(type => {
            document.addEventListener(type, (e) => {
                const sel = (document.getSelection() || '').toString();
                this.addEvent('clipboard', type, {
                    length: sel.length,
                    lines: sel.split('\n').length,
                    prefix: sel.slice(0, 60),
                    full_page: sel.length > 2000,
                    target: (e.target && (e.target.name || e.target.tagName)) || null,
                });
                console.log(`[InteractionTracker] Copy Event: ${lines}`)
            }, true);
        });
    }

    /**
     * Track click events
     * @param {Event} e - Click event
     */
    trackClick(e) {
        //console.log('[trackClick]');
        const target = this.getTargetIdentifier(e.target);
        if (target) {
            this.addEvent('click', target, {
                x: e.clientX,
                y: e.clientY,
            });
        }
    }

    /**
     * Track focus entering an element
     * @param {Event} e - Focus event
     */
    trackFocusIn(e) {
        const target = this.getTargetIdentifier(e.target);
        if (target) {
            this.focusStartTime[target] = Date.now();
            this.addEvent('focus_in', target);
        }
    }

    /**
     * Track focus leaving an element
     * @param {Event} e - Focus event
     */
    trackFocusOut(e) {
        const target = this.getTargetIdentifier(e.target);
        if (target && this.focusStartTime[target]) {
            const duration = Date.now() - this.focusStartTime[target];
            this.focusTime[target] = (this.focusTime[target] || 0) + duration;
            delete this.focusStartTime[target];
            this.addEvent('focus_out', target, { duration_ms: duration });
        }
    }

    /**
     * Track visibility change
     * @param {Event} e - event
     */
    trackVisibilityChange(e) {
        try {
            if (document.hidden) {
                // User left tab
                const hiddenAt = Date.now();
                this.addEvent('page_hidden', "page", {});
            } else {
                // User returned
                const visibleAt = Date.now();
                this.addEvent('page_visible', "page", {});
            }
        } catch (err) {
            console.warn("Visibility tracking failed:", err);
        }
    }

    /**
     * Track scroll depth
     */
    trackScroll() {
        const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
        const scrollHeight = document.documentElement.scrollHeight - window.innerHeight;
        const scrollPercent = scrollHeight > 0 ? (scrollTop / scrollHeight) * 100 : 0;
        this.scrollDepthMax = Math.max(this.scrollDepthMax, scrollPercent);
    }

    /**
     * Track keyboard shortcuts
     * @param {Event} e - Keydown event
     */
    trackKeypress(e) {
        // Track annotation-related keypresses (number keys for keybindings)
        if (e.key >= '0' && e.key <= '9') {
            this.addEvent('keypress', `key:${e.key}`, {
                ctrl: e.ctrlKey,
                alt: e.altKey,
                shift: e.shiftKey,
            });
        }

        // Track navigation shortcuts
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
            this.addEvent('keypress', `nav:${e.key}`, {
                ctrl: e.ctrlKey,
                alt: e.altKey,
            });
        }

        // Track save shortcut (Ctrl/Cmd + S)
        if ((e.ctrlKey || e.metaKey) && e.key === 's') {
            this.addEvent('keypress', 'save:shortcut');
        }

        // Track copy shortcut (Ctrl/Cmd + C)
        if ((e.ctrlKey || e.metaKey) && e.key === 'c') {
            this.addEvent('keypress', 'ctrl-c copy');
        }

        // Track copy shortcut (Ctrl/Cmd + C)
        if ((e.ctrlKey || e.metaKey) && e.key === 'v') {
            this.addEvent('keypress', 'ctrl-v paste');
        }
    }

    /**
     * Track annotation change
     * @param {string} schemaName - Schema name
     * @param {string} labelName - Label name
     * @param {string} action - Action type (select, deselect, update, clear)
     * @param {*} oldValue - Previous value
     * @param {*} newValue - New value
     * @param {string} source - What triggered the change (user, ai_accept, keyboard, prefill)
     */
    trackAnnotationChange(schemaName, labelName, action, oldValue, newValue, source = 'user') {
        /*this.addEvent('annotation_change', `schema:${schemaName}`, {
            label: labelName,
            action: action,
            old_value: oldValue,
            new_value: newValue,
            source: source,
        });*/ // Do not send to interaction endpoint !!!

        // Also send to dedicated annotation change endpoint for persistence
        this.sendAnnotationChange(schemaName, labelName, action, oldValue, newValue, source, Date.now() / 1000);
    }

    /**
     * Track navigation between instances
     * @param {string} action - Navigation action (next, prev, jump)
     * @param {string} fromInstance - Previous instance ID
     * @param {string} toInstance - New instance ID
     */
    trackNavigation(action, fromInstance, toInstance) {
        this.addEvent('navigation', action, {
            from_instance: fromInstance,
            to_instance: toInstance,
        });
    }

    /**
     * Track save action
     * @param {string} instanceId - Instance being saved
     */
    trackSave(instanceId) {
        this.addEvent('save', `instance:${instanceId || this.currentInstanceId}`);
    }

    /**
     * Get a unique identifier for an element
     * @param {Element} element - DOM element
     * @returns {string|null} - Element identifier or null
     */
    getTargetIdentifier(element) {
        if (!element || !element.closest) return null;

        // Check for annotation labels (checkbox/radio inputs or their labels)
        const labelInput = element.closest('input[type="checkbox"], input[type="radio"]');
        if (labelInput) {
            const name = labelInput.name || '';
            const value = labelInput.value || '';
            if (name) {
                return `label:${name}:${value}`;
            }
        }

        // Check for navigation buttons
        if (element.id === 'next_instance_button' || element.closest('#next_instance_button')) {
            return 'nav:next';
        }
        if (element.id === 'prev_instance_button' || element.closest('#prev_instance_button')) {
            return 'nav:prev';
        }

        // Check for training buttons
        if (element.id === 'training_submit_button') {
            return 'training:submit';
        }
        if (element.id === 'training_retry_button') {
            return 'training:retry';
        }

        return null;
    }

    /**
     * Add an event to the queue
     * @param {string} eventType - Type of event
     * @param {string} target - Target identifier
     * @param {Object} metadata - Additional metadata
     */
    addEvent(eventType, target, metadata = {}) {
        const event = {
            event_type: eventType,
            client_timestamp: Date.now() / 1000,  // Unix timestamp in seconds
            target: target,
            instance_id: this.currentInstanceId,
            metadata: metadata,
        };

        this.events.push(event);

        if (this.debugMode) {
            console.log('[InteractionTracker] Event:', event);
        }

        // Auto-flush if buffer is large
        if (this.events.length >= 50) {
            this.flush(false);
        }
    }

    /**
     * Flush events to the server
     * @param {boolean} isFinal - Whether this is a final flush (page unload)
     */
    async flush(isFinal) {
        //console.log('[InteractionTracker] Flushing');

        if (this.events.length === 0 && Object.keys(this.focusTime).length === 0) {
            return;
        }

        const payload = {
            instance_id: this.currentInstanceId,
            events: [...this.events],
            focus_time: { ...this.focusTime },
            scroll_depth: this.scrollDepthMax,
        };

        // Clear local buffers
        this.events = [];
        this.focusTime = {};

        if (this.debugMode) {
            console.log('[InteractionTracker] Flushing:', payload);
        }

        if (isFinal) {
            // Use sendBeacon for reliable delivery on page unload
            const blob = new Blob([JSON.stringify(payload)], { type: 'application/json' });
            navigator.sendBeacon('/api/track_interactions', blob);
        } else {
            try {
                await fetch('/api/track_interactions', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
            } catch (e) {
                if (this.debugMode) {
                    console.warn('[InteractionTracker] Failed to send interaction data:', e);
                }
            }
        }

        this.lastFlush = Date.now();
    }

    /**
     * Send annotation change to dedicated endpoint
     */
    async sendAnnotationChange(schemaName, labelName, action, oldValue, newValue, source, timestamp) {
        try {
            await fetch('/api/track_annotation_change', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    instance_id: this.currentInstanceId,
                    schema_name: schemaName,
                    label_name: labelName,
                    //action: action,
                    old_value: oldValue,
                    new_value: newValue,
                    source: source,
                    client_timestamp: timestamp  // Unix timestamp in seconds
                }),
            });
        } catch (e) {
            if (this.debugMode) {
                console.warn('[InteractionTracker] Failed to send annotation change:', e);
            }
        }
    }

    /**
     * Enable or disable debug mode
     * @param {boolean} enabled - Whether debug mode is enabled
     */
    setDebugMode(enabled) {
        this.debugMode = enabled;
        console.log(`[InteractionTracker] Debug mode: ${enabled ? 'enabled' : 'disabled'}`);
    }

    /**
     * Clean up tracker resources
     */
    destroy() {
        if (this.flushTimer) {
            clearInterval(this.flushTimer);
        }
        this.flush(true);
    }
}

// Create global instance
window.interactionTracker = new InteractionTracker();

// Expose for debugging
window.InteractionTracker = InteractionTracker;
