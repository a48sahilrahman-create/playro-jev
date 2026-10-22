/**
 * Playro JEV - Smart TV, Google/Android TV, Gboard & Mic Controller
 * High-performance 2D Cartesian spatial navigation, 4-tier voice search engine,
 * Gboard IME integration, player overlay D-pad controller, and modal focus trapping.
 */

(function (window, document) {
    'use strict';

    // ==========================================
    // 1. TV Environment Auto-Detection
    // ==========================================
    const TV_UA_PATTERNS = [
        /GoogleTV/i,
        /Android.*(TV|LargeScreen|Box|MIBOX|BRAVIA|AFTT|AFTM|Nexus Player)/i,
        /SmartTV/i,
        /HbbTV/i,
        /CrKey/i,
        /Tizen/i,
        /Web0S/i,
        /NetCast/i,
        /Roku/i,
        /AppleTV/i
    ];

    function detectTVEnvironment() {
        const ua = navigator.userAgent || '';
        const isTvUA = TV_UA_PATTERNS.some(pattern => pattern.test(ua));

        let hasTvParam = false;
        try {
            const urlParams = new URLSearchParams(window.location.search);
            hasTvParam = urlParams.get('tv') === '1' ||
                         urlParams.get('tv') === 'true' ||
                         urlParams.get('mode') === 'tv';
        } catch (_) {}

        let hasTvStorage = false;
        try {
            hasTvStorage = localStorage.getItem('playro_tv_mode') === 'true' ||
                           localStorage.getItem('tv_mode') === 'true' ||
                           localStorage.getItem('tvMode') === 'true';
        } catch (_) {}

        const isTv = isTvUA || hasTvParam || hasTvStorage;

        if (isTv) {
            enableTVMode(false);
        }

        return isTv;
    }

    function isTVMode() {
        return document.body.classList.contains('tv-mode') || document.documentElement.classList.contains('tv-mode');
    }

    function enableTVMode(persist = true) {
        document.body.classList.add('tv-mode');
        document.documentElement.classList.add('tv-mode');
        injectTVStyles();
        if (persist) {
            try {
                localStorage.setItem('playro_tv_mode', 'true');
            } catch (_) {}
        }
    }

    function disableTVMode() {
        document.body.classList.remove('tv-mode');
        document.documentElement.classList.remove('tv-mode');
        try {
            localStorage.setItem('playro_tv_mode', 'false');
        } catch (_) {}
    }

    function toggleTVMode() {
        if (isTVMode()) {
            disableTVMode();
        } else {
            enableTVMode(true);
        }
        return isTVMode();
    }

    function injectTVStyles() {
        if (document.getElementById('tvControllerStyles')) return;
        const style = document.createElement('style');
        style.id = 'tvControllerStyles';
        style.textContent = `
            /* 10-foot TV Focus & Visual HUD Styles */
            .tv-mode :focus,
            .tv-focused {
                outline: 3px solid #6366f1 !important;
                outline-offset: 3px !important;
                box-shadow: 0 0 20px rgba(99, 102, 241, 0.6) !important;
                transform: scale(1.03) !important;
                transition: transform 0.15s ease-out, outline-color 0.15s ease-out !important;
                z-index: 40 !important;
            }
            .tv-mode #searchInput:focus {
                transform: none !important;
                outline: 3px solid #818cf8 !important;
            }
            .tv-mode #playerProgressBar:focus {
                outline: 3px solid #f43f5e !important;
                box-shadow: 0 0 15px rgba(244, 63, 94, 0.7) !important;
            }
            .tv-mode .media-card:focus,
            .tv-mode #searchResultsGrid > div:focus {
                outline: 4px solid #6366f1 !important;
                transform: scale(1.06) !important;
            }
        `;
        document.head.appendChild(style);
    }

    // ==========================================
    // 2. 2D Cartesian Spatial Navigation Engine
    // ==========================================
    const FOCUSABLE_SELECTOR = '.focusable, button, a, input, select, textarea, [tabindex="0"], [tabindex]:not([tabindex="-1"])';

    function isElementVisible(el) {
        if (!el || el.disabled || el.getAttribute('aria-hidden') === 'true') return false;
        if (el.classList.contains('hidden') || el.style.display === 'none' || el.style.visibility === 'hidden') return false;

        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;

        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;

        // Ensure not inside a hidden modal or hidden parent
        if (el.closest('.hidden, [style*="display: none"], [style*="visibility: hidden"]')) {
            return false;
        }

        return true;
    }

    function getVisibleFocusableElements(container) {
        const root = container || document.body;
        const all = Array.from(root.querySelectorAll(FOCUSABLE_SELECTOR));
        return all.filter(isElementVisible);
    }

    /**
     * Weighted Euclidean 2D Ray-Casting Algorithm
     * primary axis delta + (orthogonal delta * 2.2) - overlap bonus
     */
    function findBestCandidate(currentEl, direction, candidates) {
        if (!candidates || candidates.length === 0) return null;
        if (!currentEl) return candidates[0] || null;

        const curRect = currentEl.getBoundingClientRect();
        const curCenter = {
            x: curRect.left + curRect.width / 2,
            y: curRect.top + curRect.height / 2
        };

        let bestCandidate = null;
        let lowestScore = Infinity;

        for (let i = 0; i < candidates.length; i++) {
            const cand = candidates[i];
            if (cand === currentEl) continue;

            const cRect = cand.getBoundingClientRect();
            const cCenter = {
                x: cRect.left + cRect.width / 2,
                y: cRect.top + cRect.height / 2
            };

            let primaryDelta = 0;
            let orthogonalDelta = 0;
            let overlap = 0;
            let isValid = false;

            switch (direction) {
                case 'ArrowDown':
                    if (cCenter.y > curCenter.y + 2 || cRect.top >= curRect.bottom - 4) {
                        isValid = true;
                        primaryDelta = (cRect.top >= curRect.bottom)
                            ? (cRect.top - curRect.bottom)
                            : Math.max(1, cCenter.y - curCenter.y);
                        orthogonalDelta = Math.abs(cCenter.x - curCenter.x);
                        overlap = Math.max(0, Math.min(curRect.right, cRect.right) - Math.max(curRect.left, cRect.left));
                    }
                    break;

                case 'ArrowUp':
                    if (cCenter.y < curCenter.y - 2 || cRect.bottom <= curRect.top + 4) {
                        isValid = true;
                        primaryDelta = (curRect.top >= cRect.bottom)
                            ? (curRect.top - cRect.bottom)
                            : Math.max(1, curCenter.y - cCenter.y);
                        orthogonalDelta = Math.abs(cCenter.x - curCenter.x);
                        overlap = Math.max(0, Math.min(curRect.right, cRect.right) - Math.max(curRect.left, cRect.left));
                    }
                    break;

                case 'ArrowRight':
                    if (cCenter.x > curCenter.x + 2 || cRect.left >= curRect.right - 4) {
                        isValid = true;
                        primaryDelta = (cRect.left >= curRect.right)
                            ? (cRect.left - curRect.right)
                            : Math.max(1, cCenter.x - curCenter.x);
                        orthogonalDelta = Math.abs(cCenter.y - curCenter.y);
                        overlap = Math.max(0, Math.min(curRect.bottom, cRect.bottom) - Math.max(curRect.top, cRect.top));
                    }
                    break;

                case 'ArrowLeft':
                    if (cCenter.x < curCenter.x - 2 || cRect.right <= curRect.left + 4) {
                        isValid = true;
                        primaryDelta = (curRect.left >= cRect.right)
                            ? (curRect.left - cRect.right)
                            : Math.max(1, curCenter.x - cCenter.x);
                        orthogonalDelta = Math.abs(cCenter.y - curCenter.y);
                        overlap = Math.max(0, Math.min(curRect.bottom, cRect.bottom) - Math.max(curRect.top, cRect.top));
                    }
                    break;
            }

            if (!isValid) continue;

            const overlapBonus = overlap > 0 ? (overlap * 0.75) : 0;
            const score = primaryDelta + (orthogonalDelta * 2.2) - overlapBonus;

            if (score < lowestScore) {
                lowestScore = score;
                bestCandidate = cand;
            }
        }

        return bestCandidate;
    }

    function getActiveDetailView() {
        const detailView = document.getElementById('mediaDetailView');
        if (detailView && !detailView.classList.contains('hidden') && detailView.style.display !== 'none') {
            return detailView;
        }
        return null;
    }

    function getActiveScrollContainer() {
        const detailView = getActiveDetailView();
        if (detailView) return detailView;

        const catalogView = document.getElementById('catalogView');
        if (catalogView && !catalogView.classList.contains('hidden') && catalogView.style.display !== 'none') {
            if (catalogView.scrollHeight > catalogView.clientHeight && window.getComputedStyle(catalogView).overflowY === 'auto') {
                return catalogView;
            }
        }
        return null;
    }

    function scrollActiveCanvas(deltaY = 380) {
        const detailView = getActiveDetailView();
        if (detailView) {
            detailView.scrollBy({ top: deltaY, behavior: 'smooth' });
            return;
        }

        const catalogView = document.getElementById('catalogView');
        if (catalogView && catalogView.scrollHeight > catalogView.clientHeight && window.getComputedStyle(catalogView).overflowY === 'auto') {
            catalogView.scrollBy({ top: deltaY, behavior: 'smooth' });
        }
        window.scrollBy({ top: deltaY, behavior: 'smooth' });
    }

    function isCandidateInView(el, container) {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;

        let topBound = 0;
        let bottomBound = window.innerHeight;

        if (container && container !== document.body) {
            const cRect = container.getBoundingClientRect();
            topBound = Math.max(topBound, cRect.top);
            bottomBound = Math.min(bottomBound, cRect.bottom);
        }

        return rect.bottom >= topBound && rect.top <= bottomBound;
    }

    function navigateSpatial(direction) {
        const activeModal = getActiveModal();
        const activePlayer = getActivePlayerOverlay();
        const activeDetail = getActiveDetailView();
        const container = activeModal || activePlayer || activeDetail || document.body;

        const allCandidates = getVisibleFocusableElements(container);
        if (allCandidates.length === 0) return false;

        let current = document.activeElement;
        if (!current || current === document.body || !allCandidates.includes(current)) {
            const firstInView = allCandidates.find(el => isCandidateInView(el, container)) || allCandidates[0];
            firstInView.focus();
            smoothScrollIntoView(firstInView);
            return true;
        }

        // For ArrowDown/ArrowUp, restrict candidate search to elements visible in the active viewport/container
        // so that reaching the lowest visible row correctly reports no candidate below and triggers smooth container scrolling
        let candidates = allCandidates;
        if (direction === 'ArrowDown') {
            const containerBottom = (container && container !== document.body)
                ? container.getBoundingClientRect().bottom
                : window.innerHeight;
            candidates = allCandidates.filter(c => c === current || c.getBoundingClientRect().top < containerBottom - 20);
        } else if (direction === 'ArrowUp') {
            const containerTop = (container && container !== document.body)
                ? container.getBoundingClientRect().top
                : 0;
            candidates = allCandidates.filter(c => c === current || c.getBoundingClientRect().bottom > containerTop + 20);
        }

        const next = findBestCandidate(current, direction, candidates);
        if (next) {
            next.focus();
            smoothScrollIntoView(next);
            return true;
        }

        return false;
    }

    function smoothScrollIntoView(element) {
        if (!element) return;

        const detailView = getActiveDetailView();
        if (detailView && detailView.contains(element) && element !== detailView) {
            const containerRect = detailView.getBoundingClientRect();
            const elRect = element.getBoundingClientRect();
            const stickyHeaderHeight = 75; // Account for top sticky header
            const bottomPadding = 40;

            if (elRect.top < containerRect.top + stickyHeaderHeight) {
                const scrollDelta = elRect.top - (containerRect.top + stickyHeaderHeight + 16);
                detailView.scrollBy({ top: scrollDelta, behavior: 'smooth' });
                return;
            } else if (elRect.bottom > containerRect.bottom - bottomPadding) {
                const scrollDelta = elRect.bottom - (containerRect.bottom - bottomPadding - 16);
                detailView.scrollBy({ top: scrollDelta, behavior: 'smooth' });
                return;
            }
            return;
        }

        try {
            element.scrollIntoView({
                behavior: 'smooth',
                block: 'nearest',
                inline: 'nearest'
            });
        } catch (_) {
            try { element.scrollIntoView(false); } catch (_) {}
        }
    }

    // ==========================================
    // 3. Gboard IME Integration
    // ==========================================
    function setupGboardIME() {
        const searchInput = document.getElementById('searchInput');
        if (!searchInput) return;

        searchInput.setAttribute('enterkeyhint', 'search');
        searchInput.setAttribute('inputmode', 'search');
        searchInput.setAttribute('autocomplete', 'off');

        searchInput.addEventListener('keydown', handleSearchInputKey, true);
    }

    function handleSearchInputKey(e) {
        const searchInput = e.target;

        // D-Pad ArrowDown: blur search input, dismiss Gboard, focus first category/card
        if (e.key === 'ArrowDown') {
            if (!isTVMode()) return; // Let desktop browsers navigate dropdown or autocomplete naturally
            e.preventDefault();
            e.stopPropagation();

            searchInput.blur();

            const firstCategory = document.querySelector('.nav-filter-btn') ||
                                  document.querySelector('.category-pill') ||
                                  document.querySelector('#categoryFilters .focusable');

            const firstCard = document.querySelector('#searchResultsGrid .focusable') ||
                              document.querySelector('#catalogGrid .focusable') ||
                              document.querySelector('.media-card');

            const target = firstCategory || firstCard;
            if (target && isElementVisible(target)) {
                target.focus();
                smoothScrollIntoView(target);
            } else {
                navigateSpatial('ArrowDown');
            }
            return;
        }

        // Enter: execute search, blur input, move focus to searchResultsGrid
        if (e.key === 'Enter') {
            e.preventDefault();
            e.stopPropagation();

            searchInput.blur();

            if (typeof window.closeAutocomplete === 'function') {
                window.closeAutocomplete();
            }

            if (typeof window.executeSearch === 'function') {
                window.executeSearch();
            }

            setTimeout(() => {
                const resultsGrid = document.getElementById('searchResultsGrid');
                if (resultsGrid) {
                    const firstResult = resultsGrid.querySelector(FOCUSABLE_SELECTOR);
                    if (firstResult && isElementVisible(firstResult)) {
                        firstResult.focus();
                        smoothScrollIntoView(firstResult);
                    } else {
                        resultsGrid.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    }
                }
            }, 300);
            return;
        }

        // Escape or Back: blur input, stay on search container without back navigation
        if (e.key === 'Escape' || e.key === 'Back' || e.keyCode === 27 || e.keyCode === 4 || e.keyCode === 10009) {
            e.preventDefault();
            e.stopPropagation();

            searchInput.blur();

            if (typeof window.closeAutocomplete === 'function') {
                window.closeAutocomplete();
            }

            const searchContainer = searchInput.closest('.relative') || searchInput.parentElement || searchInput;
            const voiceBtn = document.getElementById('voiceBtn');
            if (voiceBtn && isElementVisible(voiceBtn)) {
                voiceBtn.focus();
            } else if (searchContainer) {
                searchInput.focus();
            }
            return;
        }
    }

    // ==========================================
    // 4. 4-Tier Voice Search Engine
    // ==========================================
    let voiceRecognitionInstance = null;
    let isVoiceListening = false;

    function ensureVoiceModalDOM() {
        let modal = document.getElementById('tvVoiceModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'tvVoiceModal';
            modal.className = 'hidden fixed inset-0 z-[9999] flex flex-col items-center justify-center bg-black/85 backdrop-blur-md';
            modal.innerHTML = `
                <div class="relative flex items-center justify-center w-32 h-32 mb-6">
                    <div class="absolute inset-0 rounded-full bg-cyan-500/20 animate-ping"></div>
                    <div class="absolute inset-2 rounded-full bg-cyan-500/30 animate-pulse"></div>
                    <div class="relative flex items-center justify-center w-20 h-20 rounded-full bg-cyan-500 text-black shadow-lg shadow-cyan-500/50">
                        <svg class="w-10 h-10" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z"/></svg>
                    </div>
                </div>
                <h2 id="tvVoiceStatus" class="text-2xl font-bold text-white mb-2">Listening...</h2>
                <p id="tvVoiceTranscript" class="text-lg text-cyan-400 font-medium max-w-lg text-center px-4 min-h-[2rem]">Speak a movie, series, or anime title...</p>
                <button id="tvVoiceCancelBtn" class="mt-8 px-6 py-2.5 rounded-full bg-white/10 hover:bg-white/20 text-white font-medium focus:ring-2 focus:ring-cyan-400 focus:outline-none transition-all">Cancel (Back)</button>
            `;
            document.body.appendChild(modal);
        }

        const cancelBtn = document.getElementById('tvVoiceCancelBtn');
        if (cancelBtn) {
            cancelBtn.onclick = () => stopVoiceRecognition();
        }

        return modal;
    }

    function initVoiceSearchEngine() {
        ensureVoiceModalDOM();

        // Bind global handler for Android TV Bridge
        window.onNativeVoiceResult = function (text) {
            handleVoiceFinalResult(text);
        };

        // Remote mic hardware key listeners
        window.addEventListener('keydown', (e) => {
            // keyCode 84: KEYCODE_SEARCH
            // keyCode 174, 175: MediaSelect, VoiceSearch
            if (e.keyCode === 84 || e.keyCode === 174 || e.keyCode === 175 ||
                e.key === 'LaunchSearch' || e.key === 'Search') {
                e.preventDefault();
                e.stopPropagation();
                triggerTVVoiceSearch();
            }
        }, true);
    }

    function triggerTVVoiceSearch() {
        if (isVoiceListening) {
            stopVoiceRecognition();
            return;
        }

        // Tier 3: Check Android TV Bridge first
        if (window.AndroidTVBridge && typeof window.AndroidTVBridge.startVoiceRecognition === 'function') {
            showVoiceModal();
            try {
                window.AndroidTVBridge.startVoiceRecognition();
                return;
            } catch (err) {
                console.warn('AndroidTVBridge voice recognition failed, falling back to Web Speech:', err);
            }
        }

        // Tier 1: Web Speech API
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (SpeechRecognition) {
            startWebSpeechRecognition(SpeechRecognition);
            return;
        }

        // Tier 4: Gboard fallback (summon virtual keyboard with mic)
        triggerGboardFallback();
    }

    function startWebSpeechRecognition(SpeechRecognitionClass) {
        showVoiceModal();
        try {
            if (voiceRecognitionInstance) {
                try { voiceRecognitionInstance.abort(); } catch (_) {}
            }

            const recognition = new SpeechRecognitionClass();
            recognition.continuous = false;
            recognition.interimResults = true;
            recognition.lang = navigator.language || 'en-US';

            recognition.onstart = () => {
                isVoiceListening = true;
                updateVoiceHUD('Listening...', 'Speak movie, series, or anime title...');
            };

            recognition.onresult = (event) => {
                let interimTranscript = '';
                let finalTranscript = '';

                for (let i = event.resultIndex; i < event.results.length; ++i) {
                    if (event.results[i].isFinal) {
                        finalTranscript += event.results[i][0].transcript;
                    } else {
                        interimTranscript += event.results[i][0].transcript;
                    }
                }

                const displayTranscript = finalTranscript || interimTranscript;
                if (displayTranscript) {
                    updateVoiceHUD('Listening...', `"${displayTranscript}"`);
                }

                if (finalTranscript) {
                    handleVoiceFinalResult(finalTranscript);
                }
            };

            recognition.onerror = (e) => {
                console.warn('TV Voice Recognition error:', e);
                if (e.error === 'no-speech') {
                    updateVoiceHUD('No speech heard', 'Try again or type with Gboard');
                } else if (e.error === 'not-allowed') {
                    updateVoiceHUD('Microphone Blocked', 'Check TV permissions');
                } else {
                    updateVoiceHUD('Voice Error', e.error || 'Recognition failed');
                }
                setTimeout(() => stopVoiceRecognition(), 1500);
            };

            recognition.onend = () => {
                isVoiceListening = false;
            };

            voiceRecognitionInstance = recognition;
            recognition.start();
        } catch (err) {
            console.warn('Failed to start SpeechRecognition:', err);
            triggerGboardFallback();
        }
    }

    function triggerGboardFallback() {
        hideVoiceModal();
        const searchInput = document.getElementById('searchInput');
        if (searchInput) {
            searchInput.focus();
            if (typeof searchInput.click === 'function') searchInput.click();
        }
    }

    function handleVoiceFinalResult(text) {
        if (!text) return;
        updateVoiceHUD('Searching...', `"${text}"`);
        setTimeout(() => {
            stopVoiceRecognition();
            const searchInput = document.getElementById('searchInput');
            if (searchInput) {
                searchInput.value = text;
            }
            if (typeof window.executeSearch === 'function') {
                window.executeSearch();
            }
        }, 600);
    }

    function showVoiceModal() {
        const modal = ensureVoiceModalDOM();
        modal.classList.remove('hidden');
        isVoiceListening = true;
        updateVoiceHUD('Listening...', 'Speak movie, series, or anime title...');
        const cancelBtn = document.getElementById('tvVoiceCancelBtn');
        if (cancelBtn) cancelBtn.focus();
    }

    function hideVoiceModal() {
        const modal = document.getElementById('tvVoiceModal');
        if (modal) {
            modal.classList.add('hidden');
        }
        isVoiceListening = false;
    }

    function stopVoiceRecognition() {
        if (voiceRecognitionInstance) {
            try { voiceRecognitionInstance.stop(); } catch (_) {}
            voiceRecognitionInstance = null;
        }
        hideVoiceModal();
    }

    function updateVoiceHUD(status, transcript) {
        const statusEl = document.getElementById('tvVoiceStatus');
        const transcriptEl = document.getElementById('tvVoiceTranscript');
        if (statusEl) statusEl.textContent = status;
        if (transcriptEl) transcriptEl.textContent = transcript;
    }

    // ==========================================
    // 5. Fullscreen Player Overlay D-Pad Controller
    // ==========================================
    function getActivePlayerOverlay() {
        const overlay = document.getElementById('playerOverlay');
        if (overlay && !overlay.classList.contains('hidden') && overlay.style.display !== 'none') {
            return overlay;
        }
        return null;
    }

    function setupPlayerOverlayController() {
        window.addEventListener('keydown', handlePlayerDPad, true);
    }

    function handlePlayerDPad(e) {
        const overlay = getActivePlayerOverlay();
        if (!overlay) return;

        const video = document.getElementById('videoElement');

        // Close player overlay on Back / Escape
        if (e.key === 'Escape' || e.key === 'Back' || e.keyCode === 27 || e.keyCode === 4 || e.keyCode === 10009) {
            e.preventDefault();
            e.stopPropagation();
            if (typeof window.closeVideoPlayer === 'function') {
                window.closeVideoPlayer();
            } else {
                overlay.classList.add('hidden');
                if (video && !video.paused) video.pause();
            }
            return;
        }

        const activeEl = document.activeElement;
        const progressBar = document.getElementById('playerProgressBar');
        const isProgressBarFocused = activeEl === progressBar;

        // Remote media keys / D-pad actions
        if (e.key === 'MediaPlayPause' || e.key === 'Play' || e.key === 'Pause') {
            e.preventDefault();
            e.stopPropagation();
            if (typeof window.togglePlayPause === 'function') {
                window.togglePlayPause();
            } else if (video) {
                if (video.paused) video.play(); else video.pause();
            }
            return;
        }

        // When progress bar is focused, ArrowLeft / ArrowRight seeks
        if (isProgressBarFocused) {
            if (e.key === 'ArrowLeft') {
                e.preventDefault();
                e.stopPropagation();
                if (typeof window.skipRelative === 'function') {
                    window.skipRelative(-10);
                } else if (video) {
                    video.currentTime = Math.max(0, video.currentTime - 10);
                }
                return;
            }
            if (e.key === 'ArrowRight') {
                e.preventDefault();
                e.stopPropagation();
                if (typeof window.skipRelative === 'function') {
                    window.skipRelative(10);
                } else if (video) {
                    video.currentTime = Math.min(video.duration || 0, video.currentTime + 10);
                }
                return;
            }
        }

        // If no player button is focused, ArrowLeft / ArrowRight defaults to rewind/fast-forward
        if (!activeEl || activeEl === overlay || activeEl === document.body) {
            if (e.key === 'ArrowLeft') {
                e.preventDefault();
                if (typeof window.skipRelative === 'function') {
                    window.skipRelative(-10);
                } else if (video) {
                    video.currentTime = Math.max(0, video.currentTime - 10);
                }
                return;
            }
            if (e.key === 'ArrowRight') {
                e.preventDefault();
                if (typeof window.skipRelative === 'function') {
                    window.skipRelative(10);
                } else if (video) {
                    video.currentTime = Math.min(video.duration || 0, video.currentTime + 10);
                }
                return;
            }
            if (e.key === ' ' || e.key === 'Enter') {
                e.preventDefault();
                if (typeof window.togglePlayPause === 'function') {
                    window.togglePlayPause();
                }
                return;
            }
        }
    }

    // ==========================================
    // 6. Modal Dialog Focus Trapping
    // ==========================================
    const MODAL_IDS = [
        'tvVoiceModal',
        'audioControlPanel',
        'addonsModal',
        'configModal'
    ];

    let lastFocusedElementBeforeModal = null;

    function getActiveModal() {
        for (let i = 0; i < MODAL_IDS.length; i++) {
            const modal = document.getElementById(MODAL_IDS[i]);
            if (modal && !modal.classList.contains('hidden') && modal.style.display !== 'none') {
                return modal;
            }
        }
        return null;
    }

    function closeActiveModal(modal) {
        if (!modal) modal = getActiveModal();
        if (!modal) return;

        if (modal.id === 'tvVoiceModal') {
            stopVoiceRecognition();
        } else if (modal.id === 'audioControlPanel') {
            if (typeof window.toggleAudioPanel === 'function') {
                window.toggleAudioPanel(false);
            } else {
                modal.classList.add('hidden');
            }
        } else if (modal.id === 'addonsModal') {
            if (typeof window.closeAddonsModal === 'function') {
                window.closeAddonsModal();
            } else {
                modal.classList.add('hidden');
            }
        } else if (modal.id === 'configModal') {
            if (typeof window.closeConfigModal === 'function') {
                window.closeConfigModal();
            } else {
                modal.classList.add('hidden');
            }
        } else {
            modal.classList.add('hidden');
        }

        // Restore focus
        if (lastFocusedElementBeforeModal && isElementVisible(lastFocusedElementBeforeModal)) {
            lastFocusedElementBeforeModal.focus();
            smoothScrollIntoView(lastFocusedElementBeforeModal);
            lastFocusedElementBeforeModal = null;
        }
    }

    function setupModalFocusTrapping() {
        // Observe modal openings to trap focus and remember trigger element
        const observer = new MutationObserver(() => {
            const activeModal = getActiveModal();
            if (activeModal && !activeModal.dataset.tvTrapped) {
                activeModal.dataset.tvTrapped = 'true';
                if (document.activeElement && !activeModal.contains(document.activeElement)) {
                    lastFocusedElementBeforeModal = document.activeElement;
                }
                const focusables = getVisibleFocusableElements(activeModal);
                if (focusables.length > 0) {
                    focusables[0].focus();
                }
            } else if (!activeModal) {
                MODAL_IDS.forEach(id => {
                    const el = document.getElementById(id);
                    if (el) delete el.dataset.tvTrapped;
                });
            }
        });

        observer.observe(document.body, {
            attributes: true,
            subtree: true,
            attributeFilter: ['class', 'style']
        });

        // Intercept Escape / Back to smoothly close active modal
        window.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' || e.key === 'Back' || e.keyCode === 27 || e.keyCode === 4 || e.keyCode === 10009) {
                const activeModal = getActiveModal();
                if (activeModal) {
                    e.preventDefault();
                    e.stopPropagation();
                    closeActiveModal(activeModal);
                }
            }
        }, true);
    }

    // ==========================================
    // 7. Global Keydown Listener for 2D Spatial Nav
    // ==========================================
    function handleGlobalKeyDown(e) {
        // STRICTLY GATE all Arrow key navigation behind isTVMode()
        // If !isTVMode(), DO NOT call e.preventDefault(). Let standard desktop browsers scroll naturally with
        // ArrowDown, ArrowUp, PageDown, PageUp, Space, mouse wheel, etc.!
        if (!isTVMode()) {
            return;
        }

        const isArrow = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key);
        if (!isArrow) return;

        // If inside search input, let search input handler manage it
        if (document.activeElement && document.activeElement.id === 'searchInput') {
            return;
        }

        // If player overlay is open and activeElement is playerProgressBar, skip left/right seeking
        const playerOverlay = getActivePlayerOverlay();
        if (playerOverlay && document.activeElement && document.activeElement.id === 'playerProgressBar') {
            if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
                return; // Scrubber seeking handled in handlePlayerDPad
            }
        }

        const handled = navigateSpatial(e.key);
        if (handled) {
            e.preventDefault();
        } else if (e.key === 'ArrowDown') {
            // When user presses ArrowDown and focus is on the lowest visible row of elements with no candidate below,
            // call scrollActiveCanvas (scrolls #mediaDetailView, #catalogView, or window) to automatically reveal lower content.
            scrollActiveCanvas(380);
            e.preventDefault();
        }
    }

    function setupGlobalSpatialNav() {
        window.addEventListener('keydown', handleGlobalKeyDown);
    }

    // ==========================================
    // 8. Initialization & API Export
    // ==========================================
    function init() {
        detectTVEnvironment();
        setupGlobalSpatialNav();
        setupGboardIME();
        initVoiceSearchEngine();
        setupPlayerOverlayController();
        setupModalFocusTrapping();
    }

    // Run when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Public API Object
    const TVController = {
        detectTVEnvironment,
        isTVMode,
        enableTVMode,
        disableTVMode,
        toggleTVMode,
        navigateSpatial,
        findBestCandidate,
        getVisibleFocusableElements,
        setupGboardIME,
        handleSearchInputKey,
        initVoiceSearchEngine,
        triggerTVVoiceSearch,
        startVoiceRecognition: triggerTVVoiceSearch,
        stopVoiceRecognition,
        handleVoiceFinalResult,
        setupPlayerOverlayController,
        handlePlayerDPad,
        setupModalFocusTrapping,
        getActiveModal,
        closeActiveModal,
        getActiveDetailView,
        getActiveScrollContainer,
        scrollActiveCanvas,
        handleGlobalKeyDown,
        init
    };

    // Expose to window
    window.TVController = TVController;
    window.triggerTVVoiceSearch = triggerTVVoiceSearch;
    window.tvVoiceSearch = triggerTVVoiceSearch;
    window.initSpatialNavigation = setupGlobalSpatialNav;
    window.handleGlobalKeyDown = handleGlobalKeyDown;

})(window, document);
