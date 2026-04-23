// annotation.js - PERSISTENCE_FIX_20240124
console.log('[PERSISTENCE FIX] annotation.js loaded - Version 20240124');

// Debug logging utility - respects the debug setting from server config
function debugLog(...args) {
    if (window.config && window.config.debug) {
        console.log(...args);
    }
}

console.log('annotation.js debug:', window.config.debug);


function debugWarn(...args) {
    if (window.config && window.config.debug) {
        console.warn(...args);
    }
}

// Global state
let currentInstance = null;
let currentAnnotations = {};
let userState = null;
let isLoading = false;
let textSaveTimer = null;
let debugLastInstanceId = null;
let debugOverlayCount = 0;

// Stored event handler references for proper cleanup (prevents memory leaks)
const boundEventHandlers = {
    robustTextSelectionMouseUp: null,
    robustTextSelectionKeyUp: null
};

//let aiAssistantManger = new AIAssistantManager();

// DEEP DEBUG: Enhanced tracking
let deepDebugState = {
    navigationCalls: 0,
    instanceIdChanges: [],
    overlayStates: [],
    lastAction: null,
    timestamp: new Date().toISOString()
};

/**
 * FormLayoutManager - Manages annotation form grid layout
 *
 * Handles:
 * - CSS grid configuration from layout config
 * - Form grouping with collapsible sections
 * - Explicit ordering of forms
 * - Responsive breakpoint customization
 */
class FormLayoutManager {
    constructor() {
        this.config = null;
        this.initialized = false;
    }

    /**
     * Initialize the layout manager with configuration
     * @param {Object} layoutConfig - Layout configuration from server
     */
    initialize(layoutConfig = {}) {
        this.config = this.mergeDefaults(layoutConfig);
        this.applyGridProperties();
        this.wrapFormsInLayoutContainer();
        this.setupGroups();
        this.applyOrdering();
        this.setupResponsiveBreakpoints();
        this.initialized = true;
        debugLog('[FormLayoutManager] Initialized with config:', this.config);
    }

    /**
     * Merge user config with sensible defaults
     */
    mergeDefaults(config) {
        return {
            grid: {
                columns: 2,
                gap: '1rem',
                row_gap: null,
                align_items: 'start',
                ...config?.grid
            },
            breakpoints: {
                mobile: 480,
                tablet: 768,
                ...config?.breakpoints
            },
            styling: {
                align_items: 'start',
                content_align: 'left',
                group_background_odd: '#fafafa',
                group_background_even: '#f8f9fc',
                group_padding: '0.5rem 0.75rem',
                form_padding: '0.375rem 0.5rem',
                ...config?.styling
            },
            groups: config?.groups || [],
            order: config?.order || null
        };
    }

    /**
     * Apply grid and styling CSS custom properties to document root
     */
    applyGridProperties() {
        const root = document.documentElement;

        // Grid properties
        root.style.setProperty('--layout-columns', this.config.grid.columns);
        root.style.setProperty('--layout-gap', this.config.grid.gap);
        root.style.setProperty('--layout-row-gap', this.config.grid.row_gap || this.config.grid.gap);

        // Alignment (use styling.align_items if present, fallback to grid.align_items)
        const alignItems = this.config.styling.align_items || this.config.grid.align_items || 'start';
        root.style.setProperty('--layout-align', alignItems);

        // Content alignment
        root.style.setProperty('--layout-content-align', this.config.styling.content_align);

        // Group background colors
        root.style.setProperty('--group-bg-odd', this.config.styling.group_background_odd);
        root.style.setProperty('--group-bg-even', this.config.styling.group_background_even);

        // Padding
        root.style.setProperty('--group-padding', this.config.styling.group_padding);
        root.style.setProperty('--form-padding', this.config.styling.form_padding);
    }

    /**
     * Wrap annotation forms in a layout container
     */
    wrapFormsInLayoutContainer() {
        const container = document.getElementById('annotation-forms');
        if (!container) return;

        // Check if already wrapped
        if (container.querySelector('.annotation-forms-layout')) {
            debugLog('[FormLayoutManager] Layout container already exists');
            return;
        }

        const wrapper = document.createElement('div');
        wrapper.className = 'annotation-forms-layout';

        // Get all annotation forms
        const forms = container.querySelectorAll('.annotation-form');
        if (forms.length === 0) {
            debugLog('[FormLayoutManager] No annotation forms found');
            return;
        }

        // Move forms into wrapper
        forms.forEach(form => {
            // Set default data-grid-columns if not present
            if (!form.hasAttribute('data-grid-columns')) {
                form.setAttribute('data-grid-columns', '1');
            }
            wrapper.appendChild(form);
        });

        // Insert wrapper at the beginning of the container (after any pairwise display)
        const pairwiseDisplay = container.querySelector('.pairwise-items-display-container');
        if (pairwiseDisplay) {
            pairwiseDisplay.after(wrapper);
        } else {
            container.insertBefore(wrapper, container.firstChild);
        }

        debugLog('[FormLayoutManager] Wrapped', forms.length, 'forms in layout container');
    }

    /**
     * Setup form groups with headers and collapsible behavior
     */
    setupGroups() {
        if (!this.config.groups || this.config.groups.length === 0) return;

        const container = document.querySelector('.annotation-forms-layout') ||
                          document.querySelector('.annotation-forms-grid');
        if (!container) return;

        this.config.groups.forEach(groupConfig => {
            const groupElement = this.createGroupElement(groupConfig, container);
            if (groupElement) {
                // Move specified schemas into the group
                groupConfig.schemas.forEach(schemaName => {
                    const form = container.querySelector(`[data-schema-name="${schemaName}"]`);
                    if (form) {
                        const content = groupElement.querySelector('.annotation-form-group-content');
                        if (content) {
                            content.appendChild(form);
                        }
                    }
                });

                // Insert the group into the container
                container.appendChild(groupElement);
            }
        });

        debugLog('[FormLayoutManager] Setup', this.config.groups.length, 'groups');
    }

    /**
     * Create a group element with header and content container
     */
    createGroupElement(groupConfig, container) {
        const group = document.createElement('div');
        group.className = 'annotation-form-group';
        group.id = `group-${groupConfig.id}`;

        // Apply per-group custom background color if specified
        if (groupConfig.background_color) {
            group.style.setProperty('--group-bg', groupConfig.background_color);
            group.style.backgroundColor = groupConfig.background_color;
        }

        if (groupConfig.collapsed_default) {
            group.classList.add('collapsed');
        }

        let headerHtml = `
            <div class="annotation-form-group-header">
                <div>
                    ${groupConfig.title ? `<h4 class="annotation-form-group-title">${this.escapeHtml(groupConfig.title)}</h4>` : ''}
                    ${groupConfig.description ? `<p class="annotation-form-group-description">${this.escapeHtml(groupConfig.description)}</p>` : ''}
                </div>
        `;

        if (groupConfig.collapsible) {
            headerHtml += `
                <button type="button" class="annotation-form-group-toggle" aria-label="Toggle group">
                    <i class="fas fa-chevron-down"></i>
                </button>
            `;
        }

        headerHtml += '</div>';

        group.innerHTML = headerHtml + '<div class="annotation-form-group-content"></div>';

        // Setup toggle behavior
        if (groupConfig.collapsible) {
            const toggle = group.querySelector('.annotation-form-group-toggle');
            toggle.addEventListener('click', () => {
                group.classList.toggle('collapsed');
            });
        }

        return group;
    }

    /**
     * Apply explicit ordering to forms
     */
    applyOrdering() {
        const container = document.querySelector('.annotation-forms-layout') ||
                          document.querySelector('.annotation-forms-grid');
        if (!container) return;

        // Apply order from config.order array
        if (this.config.order && Array.isArray(this.config.order)) {
            this.config.order.forEach((schemaName, index) => {
                const form = container.querySelector(`[data-schema-name="${schemaName}"]`);
                if (form) {
                    form.style.order = index;
                }
            });
        }

        // Also apply order from data-grid-order attributes
        const formsWithOrder = container.querySelectorAll('[data-grid-order]');
        formsWithOrder.forEach(form => {
            const order = parseInt(form.getAttribute('data-grid-order'), 10);
            if (!isNaN(order)) {
                form.style.order = order;
            }
        });
    }

    /**
     * Setup custom responsive breakpoints via media query injection
     */
    setupResponsiveBreakpoints() {
        const mobile = this.config.breakpoints.mobile;
        const tablet = this.config.breakpoints.tablet;

        // Only inject custom breakpoints if they differ from defaults
        if (mobile !== 480 || tablet !== 768) {
            const styleId = 'layout-breakpoints-custom';
            let styleEl = document.getElementById(styleId);
            if (!styleEl) {
                styleEl = document.createElement('style');
                styleEl.id = styleId;
                document.head.appendChild(styleEl);
            }

            styleEl.textContent = `
                @media (max-width: ${mobile}px) {
                    .annotation-forms-layout,
                    .annotation-forms-grid {
                        --layout-columns: 1 !important;
                    }
                    .annotation-forms-layout .annotation-form[data-grid-columns],
                    .annotation-forms-grid .annotation-form[data-grid-columns] {
                        grid-column: span 1 !important;
                    }
                }
                @media (min-width: ${mobile + 1}px) and (max-width: ${tablet}px) {
                    .annotation-forms-layout .annotation-form[data-grid-columns="3"],
                    .annotation-forms-layout .annotation-form[data-grid-columns="4"],
                    .annotation-forms-layout .annotation-form[data-grid-columns="5"],
                    .annotation-forms-layout .annotation-form[data-grid-columns="6"],
                    .annotation-forms-grid .annotation-form[data-grid-columns="3"],
                    .annotation-forms-grid .annotation-form[data-grid-columns="4"],
                    .annotation-forms-grid .annotation-form[data-grid-columns="5"],
                    .annotation-forms-grid .annotation-form[data-grid-columns="6"] {
                        grid-column: span 2;
                    }
                }
            `;
        }
    }

    /**
     * Helper to escape HTML
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Global FormLayoutManager instance
window.formLayoutManager = new FormLayoutManager();

/**
 * Deep debug logging for navigation events - only logs when debug mode is enabled
 */
function logDeepDebug(action, extraData = {}) {
    // Skip all debug logging when not in debug mode
    if (!window.config || !window.config.debug) {
        return;
    }

    const state = {
        timestamp: new Date().toISOString(),
        action: action,
        currentInstanceId: currentInstance?.id,
        debugLastInstanceId: debugLastInstanceId,
        isLoading: isLoading,
        overlayCount: getCurrentOverlayCount(),
        spanManagerExists: !!window.spanManager,
        spanManagerInitialized: window.spanManager?.isInitialized,
        ...extraData
    };

    debugLog(`[DEEP DEBUG NAV] ${action}:`, state);
    deepDebugState.lastAction = action;
    deepDebugState.timestamp = new Date().toISOString();

    // Track instance ID changes
    if (extraData.newInstanceId || extraData.currentInstanceId) {
        deepDebugState.instanceIdChanges.push({
            timestamp: new Date().toISOString(),
            from: debugLastInstanceId,
            to: extraData.newInstanceId || extraData.currentInstanceId,
            action: action
        });
    }

    // Track overlay states
    deepDebugState.overlayStates.push({
        timestamp: new Date().toISOString(),
        action: action,
        overlayCount: getCurrentOverlayCount(),
        instanceId: currentInstance?.id
    });

    // Keep only last 20 entries to avoid memory bloat
    if (deepDebugState.instanceIdChanges.length > 20) {
        deepDebugState.instanceIdChanges = deepDebugState.instanceIdChanges.slice(-20);
    }
    if (deepDebugState.overlayStates.length > 20) {
        deepDebugState.overlayStates = deepDebugState.overlayStates.slice(-20);
    }
}

/**
 * Get current overlay count for debugging
 */
function getCurrentOverlayCount() {
    const spanOverlays = document.getElementById('span-overlays');
    return spanOverlays ? spanOverlays.children.length : 0;
}

// Initialize the application
document.addEventListener('DOMContentLoaded', function () {
    loadCurrentInstance();
    setupEventListeners();
    // Initial validation check
    validateRequiredFields();
    // Initialize display logic for conditional schemas
    if (typeof initDisplayLogic === 'function') {
        initDisplayLogic();
    }
    // Initialize form layout manager (if layout config is available)
    // Layout config is passed via ui_config from the server
    const layoutConfig = window.config?.ui_config?.layout || window.config?.layout;
    if (layoutConfig) {
        window.formLayoutManager.initialize(layoutConfig);
    }
    // Initialize pairwise annotation
    // initPairwiseAnnotation();
});

/**
 * Global overlay tracking for debugging - only logs when debug mode is enabled
 */
function trackOverlayCreation(overlay, context = 'unknown') {
    if (!window.config || !window.config.debug) return;

    debugLog(`[DEBUG] OVERLAY CREATED in ${context}:`, {
        className: overlay.className,
        id: overlay.id,
        parentId: overlay.parentElement?.id,
        timestamp: new Date().toISOString()
    });

    // Track total overlays
    const totalOverlays = document.querySelectorAll('.span-overlay').length;
    debugLog(`[DEBUG] TOTAL OVERLAYS after creation: ${totalOverlays}`);
}

function trackOverlayRemoval(overlay, context = 'unknown') {
    if (!window.config || !window.config.debug) return;

    debugLog(`[DEBUG] OVERLAY REMOVED in ${context}:`, {
        className: overlay.className,
        id: overlay.id,
        timestamp: new Date().toISOString()
    });

    // Track total overlays
    const totalOverlays = document.querySelectorAll('.span-overlay').length;
    debugLog(`[DEBUG] TOTAL OVERLAYS after removal: ${totalOverlays}`);
}

function debugTrackOverlays(action, instanceId = null) {
    if (!window.config || !window.config.debug) return;

    const spanOverlays = document.getElementById('span-overlays');
    const overlayCount = spanOverlays ? spanOverlays.children.length : 0;
    const instanceText = document.getElementById('instance-text');
    const textContent = document.getElementById('text-content');

    debugLog(`[DEBUG OVERLAY TRACKING] ${action}:`, {
        instanceId: instanceId || currentInstance?.id,
        lastInstanceId: debugLastInstanceId,
        overlayCount: overlayCount,
        spanOverlaysExists: !!spanOverlays,
        instanceTextExists: !!instanceText,
        textContentExists: !!textContent,
        spanOverlaysHTML: spanOverlays ? spanOverlays.innerHTML.substring(0, 200) + '...' : 'null',
        timestamp: new Date().toISOString()
    });

    debugOverlayCount = overlayCount;
    if (instanceId) debugLastInstanceId = instanceId;
}

// DEBUG: Add overlay cleanup verification - only logs when debug mode is enabled
function debugVerifyOverlayCleanup() {
    if (!window.config || !window.config.debug) return;

    const spanOverlays = document.getElementById('span-overlays');
    if (!spanOverlays) {
        debugWarn('[DEBUG] span-overlays container not found during cleanup verification');
        return;
    }

    const overlayCount = spanOverlays.children.length;
    debugLog(`[DEBUG] Overlay cleanup verification:`, {
        overlayCount: overlayCount,
        containerEmpty: overlayCount === 0,
        containerInnerHTML: spanOverlays.innerHTML,
        containerChildren: Array.from(spanOverlays.children).map(child => ({
            tagName: child.tagName,
            className: child.className,
            dataset: child.dataset
        }))
    });

    if (overlayCount > 0) {
        debugWarn('[DEBUG] WARNING: Overlays still present after expected cleanup!');
    }
}

function setupEventListeners() {
    // Go to button
    /*document.getElementById('go-to-btn').addEventListener('click', function () {
        const goToValue = document.getElementById('go_to').value;
        if (goToValue && goToValue > 0) {
            // User enters 1-based index (item 1, 2, 3...) but server uses 0-based
            navigateToInstance(parseInt(goToValue) - 1);
        }
    });

    // Enter key on go to input
    document.getElementById('go_to').addEventListener('keypress', function (e) {
        if (e.key === 'Enter') {
            document.getElementById('go-to-btn').click();
        }
    });
    */

    // Keyboard navigation and shortcuts
    document.addEventListener('keydown', function (e) {
        // Only block navigation when in text input fields (not radio/checkbox)
        const inputType = e.target.getAttribute('type');
        const isTextInput = e.target.tagName === 'TEXTAREA' ||
            (e.target.tagName === 'INPUT' && inputType !== 'radio' && inputType !== 'checkbox');

        if (isTextInput) {
            return; // Don't handle navigation when typing in text fields
        }

        switch (e.key) {
            case 'ArrowLeft':
                e.preventDefault();
                navigateToPrevious();
                break;
            case 'ArrowRight':
                e.preventDefault();
                navigateToNext();
                break;
        }
    });

    // Keyboard shortcuts for checkboxes and radio buttons (matches base_template.html behavior)
    document.addEventListener('keyup', function (e) {
        console.log("keyup")
        // Don't handle when in text input fields (but allow radio/checkbox)
        const activeElement = document.activeElement;
        const activeId = activeElement.id;
        const activeType = activeElement.getAttribute('type');
        const isTextInput = activeElement.tagName === 'TEXTAREA' ||
            activeId === 'go_to' ||
            (activeElement.tagName === 'INPUT' && activeType !== 'radio' && activeType !== 'checkbox');

        if (isTextInput) {
            return;
        }

        const key = e.key.toLowerCase();
        console.log("key:", key)

        // Check checkboxes first
        const checkboxes = document.querySelectorAll('input[type="checkbox"]');
        for (const checkbox of checkboxes) {
            if (key === checkbox.value.toLowerCase()) {
                checkbox.checked = !checkbox.checked;
                // Trigger change event so annotation state gets updated
                checkbox.dispatchEvent(new Event('change', { bubbles: true }));
                if (checkbox.onclick) {
                    checkbox.onclick.apply(checkbox);
                }
                return;
            }
        }

        // Check radio buttons
        const radios = document.querySelectorAll('input[type="radio"]');
        for (const radio of radios) {
            const dataKey = radio.getAttribute('data-key');
            if (key === radio.value.toLowerCase() || (dataKey && key === dataKey)) {
                radio.checked = true;
                // Trigger change event so annotation state gets updated
                radio.dispatchEvent(new Event('change', { bubbles: true }));
                if (radio.onclick) {
                    radio.onclick.apply(radio);
                }
                return;
            }
        }

        // Check pairwise tiles (binary mode)
        const pairwiseTiles = document.querySelectorAll('.pairwise-tile');
        for (const tile of pairwiseTiles) {
            const dataKey = tile.getAttribute('data-key');
            if (dataKey && key === dataKey) {
                selectPairwiseTile(tile);
                return;
            }
        }

        // Check pairwise tie/neither buttons
        const pairwiseButtons = document.querySelectorAll('.pairwise-tie-btn, .pairwise-neither-btn');
        for (const btn of pairwiseButtons) {
            const dataKey = btn.getAttribute('data-key');
            if (dataKey && key === dataKey) {
                selectPairwiseOption(btn);
                return;
            }
        }
    });

    console.log("added keyup eventlistener")

}

async function loadCurrentInstance() {
    try {
        setLoading(true);
        showError(false);

        // DEBUG: Track overlays at start of instance loading
        debugTrackOverlays('START_LOAD_CURRENT_INSTANCE');

        // Get current instance from server-rendered HTML
        const instanceTextElement = document.getElementById('instance-text');
        const instanceIdElement = document.getElementById('instance_id');

        if (!instanceTextElement) {
            throw new Error('Instance text element not found');
        }

        // Get instance text from the rendered HTML (server-rendered)
        const instanceText = instanceTextElement.innerHTML;

        // Get instance ID from hidden input
        const instanceId = instanceIdElement ? instanceIdElement.value : null;
        debugLog(`🔍 [DEBUG] loadCurrentInstance: Read instance_id from DOM: '${instanceId}'`);

        if (!instanceText || instanceText.trim() === '') {
            showError(true, 'No instance text available');
            return;
        }

        // Create current instance object from server-rendered data
        currentInstance = {
            id: instanceId,
            text: instanceTextElement.textContent || instanceTextElement.innerText,
            displayed_text: instanceText
        };

        // Set global variable for span manager
        window.currentInstance = currentInstance;

        // Notify interaction tracker of instance change
        if (window.interactionTracker && instanceId) {
            debugLog(`🔍 [DEBUG] loadCurrentInstance: window.interactionTracker.setInstanceId(${instanceId})`);
            window.interactionTracker.setInstanceId(instanceId);
        }

        // Get progress from the progress counter element
        const progressCounter = document.getElementById('progress-counter');
        if (progressCounter) {
            const progressText = progressCounter.textContent;
            const match = progressText.match(/(\d+)\/(\d+)/);
            if (match) {
                const annotated = parseInt(match[1]);
                const total = parseInt(match[2]);
                userState = {
                    assignments: {
                        annotated: annotated,
                        total: total
                    },
                    annotations: {
                        by_instance: {}
                    }
                };
            }
        }

        updateInstanceDisplay();

        // Clear browser-preserved form state before loading new annotations
        // This prevents image/audio/video annotations from persisting across instances
        clearAllFormInputs();

        //restoreSpanAnnotationsFromHTML();
        loadAnnotations();
        generateAnnotationForms();

        // Populate input values with existing annotations AFTER forms are generated
        setTimeout(() => {
            populateInputValues();
        }, 0);

    } catch (error) {
        console.error('Error loading current instance:', error);
        showError(true, error.message);
    } finally {
        setLoading(false);
    }
}

function updateInstanceDisplay() {
    // Instance text is already displayed in the HTML template
    // Just ensure the instance_id is set correctly
    const instanceIdInput = document.getElementById('instance_id');
    if (instanceIdInput && currentInstance && currentInstance.id) {
        const oldValue = instanceIdInput.value;
        instanceIdInput.value = currentInstance.id;
        debugLog(`🔍 [DEBUG] updateInstanceDisplay: Updated instance_id from '${oldValue}' to '${currentInstance.id}'`);
    } else {
        debugLog(`🔍 [DEBUG] updateInstanceDisplay: Could not update instance_id - input: ${!!instanceIdInput}, currentInstance: ${!!currentInstance}, currentInstance.id: ${currentInstance?.id}`);
    }
    debugLog('[DEBUG] updateInstanceDisplay: Instance display updated from server');
}

// Add this function to clear all form inputs
function clearAllFormInputs() {
    debugLog('🔍 Clearing all form inputs');

    // Clear text inputs and textareas
    const textInputs = document.querySelectorAll('input[type="text"], textarea.annotation-input');
    textInputs.forEach(input => {
        input.value = '';
    });

    // Clear radio buttons
    const radioInputs = document.querySelectorAll('input[type="radio"]');
    radioInputs.forEach(input => {
        input.checked = false;
    });

    // Clear checkboxes
    const checkboxInputs = document.querySelectorAll('input[type="checkbox"]');
    checkboxInputs.forEach(input => {
        input.checked = false;
    });

    // Clear sliders
    const sliderInputs = document.querySelectorAll('input[type="range"]');
    sliderInputs.forEach(input => {
        input.value = input.getAttribute('min') || input.getAttribute('starting_value') || '0';
        const valueDisplay = document.getElementById(`${input.name}-value`);
        if (valueDisplay) {
            valueDisplay.textContent = input.value;
        }
    });

    // Clear select dropdowns
    const selectInputs = document.querySelectorAll('select.annotation-input');
    selectInputs.forEach(input => {
        input.selectedIndex = 0;
    });

    // Clear number inputs
    const numberInputs = document.querySelectorAll('input[type="number"].annotation-input');
    numberInputs.forEach(input => {
        input.value = '';
    });

    // Clear hidden annotation data inputs (image/audio/video annotations)
    // BUT only if they don't have server-provided data (data-server-set="true")
    // This prevents browser form restoration from persisting annotations across instances
    // while preserving server-provided annotations when returning to an already-annotated instance
    const annotationDataInputs = document.querySelectorAll('input.annotation-data-input');
    annotationDataInputs.forEach(input => {
        // Only clear if NOT set by the server (prevents clearing restored annotations)
        if (input.getAttribute('data-server-set') !== 'true') {
            input.value = '';
            debugLog('🔍 Cleared annotation data input (browser-cached):', input.id);
        } else {
            debugLog('🔍 Preserving server-provided annotation data:', input.id);
        }
    });

    // Reset image annotation managers if they exist
    // BUT only if there's no server-provided annotation data to load
    const imageContainers = document.querySelectorAll('.image-annotation-container');
    imageContainers.forEach(container => {
        if (container.annotationManager && typeof container.annotationManager.clearAnnotations === 'function') {
            // Find the associated hidden input
            const schemaName = container.getAttribute('data-schema');
            const hiddenInput = schemaName ? document.getElementById('input-' + schemaName) : null;

            // Only clear if there's no server-provided data
            if (!hiddenInput || hiddenInput.getAttribute('data-server-set') !== 'true') {
                container.annotationManager.clearAnnotations();
                debugLog('🔍 Cleared image annotation manager for container (no server data)');
            } else {
                debugLog('🔍 Preserving image annotation manager (has server data)');
            }
        }
    });

    debugLog('✅ All form inputs cleared');
}

async function loadAnnotations() {
    try {
        debugLog('🔍 Loading annotations for instance:', currentInstance.id);

        // IMPORTANT: Read from server-rendered HTML attributes, NOT browser form state.
        // Firefox (and some other browsers) preserve form state across page navigations,
        // which can cause checkboxes from the previous instance to appear checked
        // even though the server didn't render them that way.

        currentAnnotations = {};

        // Read checkbox state from HTML 'checked' ATTRIBUTE (not .checked property)
        // The server sets the 'checked' attribute on checkboxes that should be checked
        const checkboxInputs = document.querySelectorAll('input[type="checkbox"]');
        checkboxInputs.forEach(input => {
            const schema = input.getAttribute('schema');
            const labelName = input.getAttribute('label_name');
            // Use hasAttribute('checked') to read server-rendered state
            const serverChecked = input.hasAttribute('checked');
            // Sync the browser state to match server state (fixes Firefox form restoration)
            input.checked = serverChecked;
            if (schema && labelName && serverChecked) {
                if (!currentAnnotations[schema]) {
                    currentAnnotations[schema] = {};
                }
                currentAnnotations[schema][labelName] = input.value;
            }
        });

        // Read radio button state from HTML 'checked' ATTRIBUTE
        const radioInputs = document.querySelectorAll('input[type="radio"]');
        radioInputs.forEach(input => {
            const schema = input.getAttribute('schema');
            const labelName = input.getAttribute('label_name');
            // Use hasAttribute('checked') to read server-rendered state
            const serverChecked = input.hasAttribute('checked');
            // Sync the browser state to match server state
            input.checked = serverChecked;
            if (schema && labelName && serverChecked) {
                if (!currentAnnotations[schema]) {
                    currentAnnotations[schema] = {};
                }
                currentAnnotations[schema][labelName] = input.value;
            }
        });

        // Read text input state from HTML
        // For text inputs, the server sets the value attribute
        // For textareas, the server sets the content between the tags (textContent)
        const textInputs = document.querySelectorAll('input[type="text"], textarea.annotation-input');
        textInputs.forEach(input => {
            const schema = input.getAttribute('schema');
            const labelName = input.getAttribute('label_name');
            // Read the server-rendered value:
            // - For <input type="text">: use getAttribute('value') which returns the HTML attribute
            // - For <textarea>: use textContent which returns the content between tags
            let serverValue;
            if (input.tagName.toLowerCase() === 'textarea') {
                serverValue = input.textContent || '';
            } else {
                serverValue = input.getAttribute('value') || '';
            }
            // Sync browser state to server state
            input.value = serverValue;
            if (schema && labelName && serverValue) {
                if (!currentAnnotations[schema]) {
                    currentAnnotations[schema] = {};
                }
                currentAnnotations[schema][labelName] = serverValue;
            }
        });

        // Read slider state from HTML 'value' ATTRIBUTE
        const sliderInputs = document.querySelectorAll('input[type="range"]');
        sliderInputs.forEach(input => {
            const schema = input.getAttribute('schema');
            const labelName = input.getAttribute('label_name');
            // Read from HTML attribute - server sets this for saved slider values
            const serverValue = input.getAttribute('value');
            if (serverValue) {
                input.value = serverValue;
            }
            if (schema && labelName) {
                if (!currentAnnotations[schema]) {
                    currentAnnotations[schema] = {};
                }
                currentAnnotations[schema][labelName] = input.value;
            }
        });

        // Read select dropdown state from server-rendered HTML
        // The server sets the 'selected' attribute on the appropriate option
        const selectInputs = document.querySelectorAll('select.annotation-input');
        selectInputs.forEach(select => {
            const schema = select.getAttribute('schema');
            const labelName = select.getAttribute('label_name');
            // Find the option with 'selected' attribute (server-rendered)
            const selectedOption = select.querySelector('option[selected]');
            if (selectedOption) {
                // Sync browser state to server state
                select.value = selectedOption.value;
            }
            if (schema && labelName && select.value) {
                if (!currentAnnotations[schema]) {
                    currentAnnotations[schema] = {};
                }
                currentAnnotations[schema][labelName] = select.value;
            }
        });

        debugLog('🔍 Annotations loaded from DOM:', currentAnnotations);
    } catch (error) {
        console.error('❌ Error loading annotations:', error);
        currentAnnotations = {};
    }
}

function generateAnnotationForms() {
    const formsContainer = document.getElementById('annotation-forms');

    // The server generates the forms, so we just need to set up event listeners
    // The forms are already in the HTML from server-side generation
    setupInputEventListeners();
    validateRequiredFields();
}

function validateRequiredFields() {
    // Check all inputs with validation="required"
    const requiredInputs = document.querySelectorAll('input[validation="required"]');
    let allRequiredFilled = true;

    // Group inputs by their name (for radio buttons) or individual inputs
    const inputGroups = {};
    requiredInputs.forEach(input => {
        if (input.type === 'radio') {
            // For radio buttons, check if any in the group is selected
            const name = input.name;
            if (!inputGroups[name]) {
                inputGroups[name] = [];
            }
            inputGroups[name].push(input);
        } else {
            // For other inputs, check individually
            if (!input.value || input.value.trim() === '') {
                allRequiredFilled = false;
            }
        }
    });

    // Check radio button groups
    for (const [name, inputs] of Object.entries(inputGroups)) {
        const anySelected = inputs.some(input => input.checked);
        if (!anySelected) {
            allRequiredFilled = false;
            break;
        }
    }

    // Update Next button state
    const nextBtn = document.getElementById('next-btn');
    if (nextBtn) {
        nextBtn.disabled = !allRequiredFilled;
    }

    return allRequiredFilled;
}

function setLoading(loading) {
    isLoading = loading;
    const loadingState = document.getElementById('loading-state');
    const mainContent = document.getElementById('main-content');
    //const prevBtn = document.getElementById('prev-btn');
    const nextBtn = document.getElementById('next-btn');

    if (loadingState) {
        if (loading) {
            loadingState.style.display = 'block';
            mainContent.style.display = 'none';
            //prevBtn.disabled = true;
            nextBtn.disabled = true;
        } else {
            loadingState.style.display = 'none';
            mainContent.style.display = 'block';
            //prevBtn.disabled = false;
            // Don't enable next button here - let validateRequiredFields handle it
            validateRequiredFields();
        }
    }
}

function showError(show, message = '') {
    const errorState = document.getElementById('error-state');
    const errorMessage = document.getElementById('error-message-text');
    const mainContent = document.getElementById('main-content');

    if (errorState) {
        if (show) {
            errorState.style.display = 'block';
            mainContent.style.display = 'none';
            errorMessage.textContent = message;
        } else {
            errorState.style.display = 'none';
            mainContent.style.display = 'block';
        }
    }
}

// Utility functions for annotation handling
function updateAnnotation(schema, label, value) {
    if (!currentAnnotations[schema]) {
        currentAnnotations[schema] = {};
    }
    currentAnnotations[schema][label] = value;
}

// Input event handling functions
function setupInputEventListeners() {
    // Set up event listeners for all annotation inputs
    const inputs = document.querySelectorAll('.annotation-input');

    inputs.forEach(input => {
        const inputType = input.type;
        const tagName = input.tagName.toLowerCase();

        if (inputType === 'text' || tagName === 'textarea') {
            // Text inputs and textareas - debounced saving
            let timer;
            input.addEventListener('input', function (event) {
                clearTimeout(timer);
                timer = setTimeout(() => {
                    handleInputChange(event.target);
                }, 1000);
            });
            debugLog(`Set up event listener for ${tagName} element:`, input.id);
        } else if (inputType === 'radio' || inputType === 'checkbox') {
            // Radio/checkbox inputs - immediate saving
            input.addEventListener('change', function (event) {
                handleInputChange(event.target);
            });
        } else if (inputType === 'range') {
            // Slider inputs - immediate saving with value display
            input.addEventListener('input', function (event) {
                const valueDisplay = document.getElementById(`${input.name}-value`);
                if (valueDisplay) {
                    valueDisplay.textContent = event.target.value;
                }
                handleInputChange(event.target);
            });
        } else if (tagName === 'select') {
            // Select inputs - immediate saving
            input.addEventListener('change', function (event) {
                handleInputChange(event.target);
            });
        } else if (inputType === 'number') {
            // Number inputs - debounced saving
            let timer;
            input.addEventListener('input', function (event) {
                clearTimeout(timer);
                timer = setTimeout(() => {
                    handleInputChange(event.target);
                }, 1000);
            });
        } else if (inputType === 'hidden') {
            // Hidden inputs (used by triage and other custom schemas) - listen for change events
            input.addEventListener('change', function (event) {
                handleInputChange(event.target);
            });
            debugLog(`Set up event listener for hidden input:`, input.id);
        }
    });
}

function handleInputChange(element) {
    const schema = element.getAttribute('schema');
    const labelName = element.getAttribute('label_name');
    const inputType = element.type;
    const tagName = element.tagName.toLowerCase();

    debugLog(`handleInputChange called for ${tagName} element:`, element.id, 'schema:', schema, 'label:', labelName);

    if (!schema || !labelName) {
        console.warn('Missing schema or label_name for input:', element);
        return;
    }

    // Validate required fields after input change
    validateRequiredFields();

    let value;

    if (inputType === 'radio') {
        // For radio buttons, only save if checked
        if (element.checked) {
            const oldValue = currentAnnotations[schema] ? currentAnnotations[schema][labelName] : null;
            value = element.value;
            // Track radio button selection
            if (window.interactionTracker) {
                window.interactionTracker.trackAnnotationChange(schema, labelName, 'select', oldValue, value, 'user');
            }
        } else {
            return; // Don't save unchecked radio buttons
        }
    } else if (inputType === 'checkbox') {
        // For checkboxes, save the checked state
        if (element.checked) {
            value = element.value;
            // Track annotation selection
            if (window.interactionTracker) {
                window.interactionTracker.trackAnnotationChange(schema, labelName, 'select', null, value, 'user');
            }
        } else {
            // For unchecked checkboxes, remove the annotation or set to false
            const oldValue = currentAnnotations[schema] ? currentAnnotations[schema][labelName] : null;
            if (currentAnnotations[schema] && currentAnnotations[schema][labelName]) {
                delete currentAnnotations[schema][labelName];
                // If the schema is empty, remove it too
                if (Object.keys(currentAnnotations[schema]).length === 0) {
                    delete currentAnnotations[schema];
                }
            }
            debugLog(`Removed annotation: ${schema}.${labelName}`);

            // Track annotation deselection
            if (window.interactionTracker) {
                window.interactionTracker.trackAnnotationChange(schema, labelName, 'deselect', oldValue, null, 'user');
            }

            return;
        }
    } else {
        // For text inputs, save the value
        const oldValue = currentAnnotations[schema] ? currentAnnotations[schema][labelName] : null;
        value = element.value;
        // Track text input change
        if (window.interactionTracker) {
            window.interactionTracker.trackAnnotationChange(schema, labelName, 'update', oldValue, value, 'user');
        }
    }

    // Update the current annotations
    updateAnnotation(schema, labelName, value);
    debugLog(`Updated annotation: ${schema}.${labelName} = ${value}`);

    // Evaluate display logic for conditional schemas
    if (displayLogicManager) {
        displayLogicManager.evaluateForSchema(schema);
    }

    // Auto-save
    clearTimeout(textSaveTimer);
}

function populateInputValues() {
    if (!currentAnnotations || !userState) return;

    debugLog('🔍 Populating input values with annotations:', currentAnnotations);

    // Populate text inputs and textareas
    const textInputs = document.querySelectorAll('input[type="text"], textarea.annotation-input');
    debugLog('🔍 Found text inputs and textareas:', textInputs.length);

    textInputs.forEach(input => {
        const schema = input.getAttribute('schema');
        const labelName = input.getAttribute('label_name');
        debugLog('🔍 Checking input:', input.id, 'schema:', schema, 'label:', labelName);

        if (schema && labelName && currentAnnotations[schema] && currentAnnotations[schema][labelName]) {
            input.value = currentAnnotations[schema][labelName];
            debugLog(`✅ Populated ${input.tagName} ${input.id} with value:`, currentAnnotations[schema][labelName]);
        } else {
            debugLog(`❌ Could not populate ${input.tagName} ${input.id}:`, {
                hasSchema: !!schema,
                hasLabelName: !!labelName,
                hasSchemaInAnnotations: !!(currentAnnotations[schema]),
                hasLabelInSchema: !!(currentAnnotations[schema] && currentAnnotations[schema][labelName])
            });
        }
    });

    // Populate radio buttons
    const radioInputs = document.querySelectorAll('input[type="radio"]');
    radioInputs.forEach(input => {
        const schema = input.getAttribute('schema');
        const labelName = input.getAttribute('label_name');

        if (schema && labelName && currentAnnotations[schema] && currentAnnotations[schema][labelName]) {
            input.checked = (currentAnnotations[schema][labelName] === input.value);
            debugLog(`Populated radio ${input.id}: ${input.checked ? 'checked' : 'unchecked'}`);
        }
    });

    // Populate checkboxes
    const checkboxInputs = document.querySelectorAll('input[type="checkbox"]');
    checkboxInputs.forEach(input => {
        const schema = input.getAttribute('schema');
        const labelName = input.getAttribute('label_name');

        if (schema && labelName && currentAnnotations[schema]) {
            // For checkboxes, check if the value exists in the annotations
            const hasAnnotation = currentAnnotations[schema][labelName] === input.value;
            input.checked = hasAnnotation;
            debugLog(`Populated checkbox ${input.id}: ${hasAnnotation ? 'checked' : 'unchecked'}`);
        }
    });

    // Populate sliders
    const sliderInputs = document.querySelectorAll('input[type="range"]');
    sliderInputs.forEach(input => {
        const schema = input.getAttribute('schema');
        const labelName = input.getAttribute('label_name');

        if (schema && labelName && currentAnnotations[schema] && currentAnnotations[schema][labelName]) {
            input.value = currentAnnotations[schema][labelName];
            const valueDisplay = document.getElementById(`${input.name}-value`);
            if (valueDisplay) {
                valueDisplay.textContent = currentAnnotations[schema][labelName];
            }
            debugLog(`Populated slider ${input.id} with value:`, currentAnnotations[schema][labelName]);
        }
    });

    // Populate select dropdowns
    const selectInputs = document.querySelectorAll('select.annotation-input');
    selectInputs.forEach(input => {
        const schema = input.getAttribute('schema');
        const labelName = input.getAttribute('label_name');

        if (schema && labelName && currentAnnotations[schema] && currentAnnotations[schema][labelName]) {
            input.value = currentAnnotations[schema][labelName];
            debugLog(`Populated select ${input.id} with value:`, currentAnnotations[schema][labelName]);
        }
    });

    // Populate number inputs
    const numberInputs = document.querySelectorAll('input[type="number"].annotation-input');
    numberInputs.forEach(input => {
        const schema = input.getAttribute('schema');
        const labelName = input.getAttribute('label_name');

        if (schema && labelName && currentAnnotations[schema] && currentAnnotations[schema][labelName]) {
            input.value = currentAnnotations[schema][labelName];
            debugLog(`Populated number ${input.id} with value:`, currentAnnotations[schema][labelName]);
        }
    });

    // Populate pairwise annotations
    // restorePairwiseAnnotations();

    validateRequiredFields();
}

// Span annotation functions
function onlyOne(checkbox) {
    debugLog('🔍 [DEBUG] onlyOne() called with checkbox:', {
        id: checkbox.id,
        name: checkbox.name,
        value: checkbox.value,
        checked: checkbox.checked,
        className: checkbox.className
    });

    var x = document.getElementsByClassName(checkbox.className);
    debugLog('🔍 [DEBUG] onlyOne() - Found elements with same class:', x.length);

    var i;
    for (i = 0; i < x.length; i++) {
        debugLog('🔍 [DEBUG] onlyOne() - Processing element:', {
            id: x[i].id,
            value: x[i].value,
            checked: x[i].checked,
            willUncheck: x[i].value != checkbox.value
        });

        if (x[i].value != checkbox.value) {
            debugLog('🔍 [DEBUG] onlyOne() - Unchecking element:', x[i].id);
            x[i].checked = false;
        }
    }
    // Ensure the clicked checkbox is checked
    debugLog('🔍 [DEBUG] onlyOne() - Setting clicked checkbox to checked:', checkbox.id);
    checkbox.setAttribute('data-just-checked', 'true'); // Flag to prevent change event interference
    checkbox.checked = true;

    // Remove the flag after a short delay in case the change event doesn't fire
    setTimeout(() => {
        if (checkbox.hasAttribute('data-just-checked')) {
            debugLog('🔍 [DEBUG] onlyOne() - Removing data-just-checked flag after timeout');
            checkbox.removeAttribute('data-just-checked');
        }
    }, 100);
}

/**
 * Show a notification message to the user.
 * @param {string} message - The message to display
 * @param {string} type - The type of notification ('info', 'success', 'warning', 'error')
 */
function showNotification(message, type = 'info') {
    // Check if a notification container exists, create if not
    let container = document.getElementById('notification-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'notification-container';
        container.style.cssText = 'position: fixed; top: 80px; right: 20px; z-index: 9999;';
        document.body.appendChild(container);
    }

    // Create the notification element
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.style.cssText = `
        padding: 12px 20px;
        margin-bottom: 10px;
        border-radius: 6px;
        font-size: 14px;
        font-weight: 500;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        animation: slideIn 0.3s ease;
        background-color: ${type === 'info' ? '#e0f2fe' : type === 'success' ? '#dcfce7' : type === 'warning' ? '#fef3c7' : '#fee2e2'};
        color: ${type === 'info' ? '#0369a1' : type === 'success' ? '#166534' : type === 'warning' ? '#92400e' : '#dc2626'};
        border: 1px solid ${type === 'info' ? '#7dd3fc' : type === 'success' ? '#86efac' : type === 'warning' ? '#fcd34d' : '#fca5a5'};
    `;
    notification.textContent = message;

    container.appendChild(notification);

    // Auto-remove after 4 seconds
    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => notification.remove(), 300);
    }, 4000);
}

// Make the function available globally for debugging
window.showNotification = showNotification;
window.loadCurrentInstance = loadCurrentInstance;
