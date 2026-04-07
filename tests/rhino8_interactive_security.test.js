const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');
const htmlPath = path.join(
  repoRoot,
  'ngrok_tunneling_this_has_port_to_INTERNET',
  'Rhino8_cheat_sheet_timestamps_interactive.html'
);

function extractInlineScript(htmlSource) {
  const match = htmlSource.match(/<script>([\s\S]*)<\/script>\s*<\/body>/i);
  if (!match) {
    throw new Error('Could not locate the inline script block in the HTML file.');
  }
  return match[1];
}

function createClassList(initialValues = []) {
  const values = new Set(initialValues);
  return {
    add(value) {
      values.add(value);
    },
    remove(value) {
      values.delete(value);
    },
    toggle(value, force) {
      if (typeof force === 'boolean') {
        if (force) {
          values.add(value);
        } else {
          values.delete(value);
        }
        return force;
      }

      if (values.has(value)) {
        values.delete(value);
        return false;
      }

      values.add(value);
      return true;
    },
    contains(value) {
      return values.has(value);
    },
    has(value) {
      return values.has(value);
    },
    toArray() {
      return Array.from(values);
    },
  };
}

function createMockElement(id) {
  const listeners = new Map();
  return {
    id,
    attributes: {},
    style: {
      display: '',
    },
    classList: createClassList(),
    children: [],
    innerHTML: '',
    textContent: '',
    value: '',
    checked: false,
    scrollTop: 0,
    scrollHeight: 0,
    listeners,
    onclick: null,
    parentNode: null,
    clickCount: 0,
    addEventListener(type, handler) {
      listeners.set(type, handler);
    },
    dispatchEvent(event) {
      const handler = listeners.get(event.type);
      if (handler) {
        handler.call(this, event);
      }
      const onHandler = this['on' + event.type];
      if (typeof onHandler === 'function') {
        onHandler.call(this, event);
      }
    },
    appendChild(child) {
      child.parentNode = this;
      this.children.push(child);
      this.scrollHeight = this.children.length;
      return child;
    },
    removeChild(child) {
      this.children = this.children.filter((item) => item !== child);
      this.scrollHeight = this.children.length;
    },
    remove() {
      if (this.parentNode && typeof this.parentNode.removeChild === 'function') {
        this.parentNode.removeChild(this);
      }
    },
    click() {
      this.clickCount += 1;
      const handler = this.listeners.get('click');
      if (handler) {
        handler.call(this, { type: 'click', target: this, preventDefault() {} });
      }
      if (typeof this.onclick === 'function') {
        this.onclick({ type: 'click', target: this, preventDefault() {} });
      }
    },
    getAttribute(name) {
      return this.attributes[name];
    },
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
  };
}

function createTimestampLink(timeValue) {
  const link = createMockElement('timestamp');
  link.attributes['data-time'] = timeValue;
  link.preventDefaultCalled = false;
  link.click = function() {
    const handler = this.listeners.get('click');
    if (!handler) {
      throw new Error('Timestamp click handler was not attached.');
    }
    handler.call(link, {
      type: 'click',
      target: link,
      preventDefault() {
        link.preventDefaultCalled = true;
      },
    });
  };
  return link;
}

function createContext(options = {}) {
  const htmlSource = fs.readFileSync(htmlPath, 'utf8');
  const scriptSource = extractInlineScript(htmlSource);

  const timers = [];
  const alerts = [];
  const cssVars = {};
  const printCalls = [];
  const blobParts = [];
  const createdUrls = [];
  const revokedUrls = [];
  const createdAnchors = [];
  const storedValues = new Map(Object.entries(options.localStorage || {}));
  const shouldThrowOnStorage = options.throwOnStorage || false;
  const scrollCalls = [];
  const ytCalls = [];
  const timestampLink = createTimestampLink(options.timestampTime || '02:34');
  const menuModal = createMockElement('menuModal');
  const menuBtn = createMockElement('menuBtn');
  const closeModal = createMockElement('closeModal');
  const themeToggle = createMockElement('themeToggle');
  const videoSizeSlider = createMockElement('videoSizeSlider');
  const printPageBtn = createMockElement('printPageBtn');
  const downloadHtmlBtn = createMockElement('downloadHtmlBtn');
  const videoErrorModal = createMockElement('videoErrorModal');
  const closeVideoErrorModal = createMockElement('closeVideoErrorModal');
  const chatBox = createMockElement('chatBox');
  const chatInput = createMockElement('chatInput');
  const sendBtn = createMockElement('sendBtn');
  const tabButtons = ['tab-info', 'tab-settings', 'tab-chat'].map((tabId, index) => {
    const button = createMockElement(`tab-btn-${index}`);
    button.attributes['data-tab'] = tabId;
    return button;
  });
  const tabContents = ['tab-info', 'tab-settings', 'tab-chat'].map((tabId) => {
    const content = createMockElement(tabId);
    content.id = tabId;
    return content;
  });
  const body = createMockElement('body');
  body.classList = createClassList();
  const documentElement = {
    style: {
      setProperty(name, value) {
        cssVars[name] = value;
      },
    },
    outerHTML: '<html><head></head><body>mock</body></html>',
  };

  const elementMap = {
    menuModal,
    menuBtn,
    closeModal,
    themeToggle,
    videoSizeSlider,
    printPageBtn,
    downloadHtmlBtn,
    videoErrorModal,
    closeVideoErrorModal,
    chatBox,
    chatInput,
    sendBtn,
    player: createMockElement('player'),
  };

  closeVideoErrorModal.textContent = 'Okay, I just want to see hotkeys';
  closeVideoErrorModal.classList.add('hotkeys-btn');

  const document = {
    body,
    documentElement,
    createElement(tagName) {
      if (tagName === 'script') {
        return { tagName, src: '' };
      }
      const element = createMockElement(tagName);
      element.tagName = String(tagName).toUpperCase();
      if (element.tagName === 'A') {
        createdAnchors.push(element);
      }
      return element;
    },
    getElementsByTagName(tagName) {
      if (tagName === 'script') {
        return [{ parentNode: { insertBefore() {} } }];
      }
      return [];
    },
    querySelectorAll(selector) {
      if (selector === '.ts') {
        return [timestampLink];
      }
      if (selector === '.tab-btn' || selector === '#menuModal .tab-btn') {
        return tabButtons;
      }
      if (selector === '.tab-content' || selector === '#menuModal .tab-content') {
        return tabContents;
      }
      return [];
    },
    getElementById(id) {
      if (id in elementMap) {
        return elementMap[id];
      }
      if (id === 'tab-info') {
        return tabContents[0];
      }
      if (id === 'tab-settings') {
        return tabContents[1];
      }
      if (id === 'tab-chat') {
        return tabContents[2];
      }
      return null;
    },
    addEventListener() {},
  };

  function BlobMock(parts, optionsArg) {
    if (options.throwOnBlob) {
      throw new Error('blob not available');
    }
    blobParts.push({ parts, options: optionsArg });
    this.parts = parts;
    this.options = optionsArg;
  }

  const URLMock = {
    createObjectURL(blob) {
      const url = `blob:mock-${createdUrls.length + 1}`;
      createdUrls.push({ url, blob });
      return url;
    },
    revokeObjectURL(url) {
      revokedUrls.push(url);
    },
  };

  const windowObject = {
    innerWidth: options.innerWidth ?? 1280,
    location: {
      protocol: options.locationProtocol ?? 'https:',
    },
    localStorage: {
      getItem(key) {
        if (shouldThrowOnStorage) {
          throw new Error('storage unavailable');
        }
        return storedValues.has(key) ? storedValues.get(key) : null;
      },
      setItem(key, value) {
        if (shouldThrowOnStorage) {
          throw new Error('storage unavailable');
        }
        storedValues.set(key, String(value));
      },
    },
    matchMedia(query) {
      return {
        matches: Boolean(options.systemPrefersDark),
        media: query,
        addEventListener() {},
        removeEventListener() {},
      };
    },
    scrollTo(optionsObject) {
      scrollCalls.push(optionsObject);
    },
    alert(message) {
      alerts.push(message);
    },
    print() {
      printCalls.push(true);
    },
    setTimeout(handler, delay) {
      timers.push({ handler, delay });
      return timers.length;
    },
    clearTimeout() {},
    YT: {
      Player: function Player(playerId, config) {
        const seekCalls = [];
        const playCalls = [];
        const player = {
          seekTo(seconds, allowSeekAhead) {
            seekCalls.push({ seconds, allowSeekAhead });
          },
          playVideo() {
            playCalls.push(true);
          },
          seekCalls,
          playCalls,
          playerId,
          config,
        };
        ytCalls.push(player);
        return player;
      },
    },
  };

  const context = {
    console,
    document,
    window: windowObject,
    navigator: { userAgent: 'node-test' },
    setTimeout: windowObject.setTimeout,
    clearTimeout: windowObject.clearTimeout,
    alert: windowObject.alert,
    innerWidth: windowObject.innerWidth,
    localStorage: windowObject.localStorage,
    matchMedia: windowObject.matchMedia.bind(windowObject),
    scrollTo: windowObject.scrollTo,
    YT: windowObject.YT,
    Blob: BlobMock,
    URL: URLMock,
    Date,
  };

  context.globalThis = context;
  context.window.window = windowObject;
  context.window.document = document;
  context.window.alert = windowObject.alert;
  context.window.localStorage = windowObject.localStorage;
  context.window.matchMedia = windowObject.matchMedia;
  context.window.scrollTo = windowObject.scrollTo;
  context.window.setTimeout = windowObject.setTimeout;
  context.window.clearTimeout = windowObject.clearTimeout;
  context.window.YT = windowObject.YT;
  context.window.URL = URLMock;
  context.window.Blob = BlobMock;
  context.window.location = windowObject.location;
  context.document = document;
  context.global = context;

  vm.runInNewContext(scriptSource, context, { filename: htmlPath });

  return {
    context,
    document,
    window: windowObject,
    alerts,
    timers,
    cssVars,
    scrollCalls,
    ytCalls,
    printCalls,
    blobParts,
    createdUrls,
    revokedUrls,
    createdAnchors,
    storedValues,
    timestampLink,
    menuModal,
    menuBtn,
    closeModal,
    themeToggle,
    videoSizeSlider,
    printPageBtn,
    downloadHtmlBtn,
    videoErrorModal,
    closeVideoErrorModal,
    chatBox,
    chatInput,
    sendBtn,
    tabButtons,
    tabContents,
    bodyClassList: body.classList,
  };
}

function createDefaultSuite(overrides = {}) {
  return createContext({
    localStorage: {
      'rhino-cheat-sheet-theme': 'dark',
      'rhino-cheat-sheet-video-width': '999',
      ...(overrides.localStorage || {}),
    },
    systemPrefersDark: false,
    innerWidth: 1280,
    timestampTime: '02:34',
    ...overrides,
  });
}

function runTimerByDelay(suite, delay) {
  const timer = suite.timers.find((item) => item.delay === delay);
  assert.ok(timer, `Expected timer with delay ${delay}`);
  timer.handler();
}

function runTest(name, fn) {
  try {
    fn();
    console.log(`PASS ${name}`);
    return true;
  } catch (error) {
    console.error(`FAIL ${name}`);
    console.error(error.stack || error.message || error);
    return false;
  }
}

let passed = 0;
let failed = 0;

const tests = [
  ['parseTimeStr accepts mm:ss format', () => {
    const suite = createDefaultSuite();
    assert.equal(suite.context.parseTimeStr('02:34'), 154);
  }],
  ['parseTimeStr accepts h:mm:ss format', () => {
    const suite = createDefaultSuite();
    assert.equal(suite.context.parseTimeStr('1:23:37'), 5017);
  }],
  ['parseTimeStr trims outer whitespace', () => {
    const suite = createDefaultSuite();
    assert.equal(suite.context.parseTimeStr(' 03:05 '), 185);
  }],
  ['parseTimeStr rejects alpha input', () => {
    const suite = createDefaultSuite();
    assert.equal(suite.context.parseTimeStr('bad-input'), 0);
  }],
  ['parseTimeStr rejects extra segments', () => {
    const suite = createDefaultSuite();
    assert.equal(suite.context.parseTimeStr('1:2:3:4'), 0);
  }],
  ['parseTimeStr rejects negative values', () => {
    const suite = createDefaultSuite();
    assert.equal(suite.context.parseTimeStr('-1:20'), 0);
  }],
  ['parseTimeStr rejects decimal values', () => {
    const suite = createDefaultSuite();
    assert.equal(suite.context.parseTimeStr('1.5:20'), 0);
  }],
  ['parseTimeStr handles null safely', () => {
    const suite = createDefaultSuite();
    assert.equal(suite.context.parseTimeStr(null), 0);
  }],
  ['parseTimeStr handles empty string safely', () => {
    const suite = createDefaultSuite();
    assert.equal(suite.context.parseTimeStr(''), 0);
  }],
  ['timestamp click shows waiting alert before failure threshold', () => {
    const suite = createDefaultSuite();
    suite.context.isPlayerReady = false;
    suite.timestampLink.click();
    assert.equal(suite.alerts.pop(), 'The YouTube video is still connecting. Please wait one second and try again.');
    assert.equal(suite.videoErrorModal.style.display, '');
  }],
  ['timestamp click shows popup on local file protocol', () => {
    const suite = createDefaultSuite({ locationProtocol: 'file:' });
    suite.context.isPlayerReady = false;
    suite.timestampLink.click();
    assert.equal(suite.videoErrorModal.style.display, 'flex');
    assert.equal(suite.videoErrorModal.getAttribute('aria-hidden'), 'false');
  }],
  ['timestamp click shows popup after elapsed boot time', () => {
    const suite = createDefaultSuite();
    suite.context.isPlayerReady = false;
    suite.context.pageBootMs = Date.now() - 2000;
    suite.timestampLink.click();
    assert.equal(suite.videoErrorModal.style.display, 'flex');
  }],
  ['timer marks YT failure when player stays unready', () => {
    const suite = createDefaultSuite();
    suite.context.isPlayerReady = false;
    runTimerByDelay(suite, 3500);
    assert.equal(suite.context.ytConnectionLikelyFailed, true);
    assert.equal(suite.context.videoErrorShownAutomatically, true);
    assert.equal(suite.videoErrorModal.style.display, 'flex');
  }],
  ['startup timer does not auto-popup when player is already ready', () => {
    const suite = createDefaultSuite();
    suite.context.isPlayerReady = true;
    runTimerByDelay(suite, 3500);
    assert.notEqual(suite.videoErrorModal.style.display, 'flex');
  }],
  ['onYouTubeIframeAPIReady clears failure flag on ready callback', () => {
    const suite = createDefaultSuite();
    suite.context.ytConnectionLikelyFailed = true;
    suite.context.onYouTubeIframeAPIReady();
    const createdPlayer = suite.ytCalls.at(-1);
    createdPlayer.config.events.onReady({});
    assert.equal(suite.context.isPlayerReady, true);
    assert.equal(suite.context.ytConnectionLikelyFailed, false);
  }],
  ['onYouTubeIframeAPIReady sets failure flag on error callback', () => {
    const suite = createDefaultSuite();
    suite.context.onYouTubeIframeAPIReady();
    const createdPlayer = suite.ytCalls.at(-1);
    createdPlayer.config.events.onError({});
    assert.equal(suite.context.ytConnectionLikelyFailed, true);
    assert.equal(suite.context.videoErrorShownAutomatically, true);
    assert.equal(suite.videoErrorModal.style.display, 'flex');
  }],
  ['menu tab switching does not hide video error popup content', () => {
    const suite = createDefaultSuite();
    suite.tabButtons[1].onclick();
    suite.context.showVideoErrorPopup();
    assert.equal(suite.videoErrorModal.style.display, 'flex');
    suite.tabButtons[2].onclick();
    assert.equal(suite.videoErrorModal.style.display, 'flex');
  }],
  ['timestamp click seeks and plays when ready', () => {
    const suite = createDefaultSuite();
    const player = {
      seekCalls: [],
      seekTo(seconds, allowSeekAhead) {
        this.seekCalls.push({ seconds, allowSeekAhead });
      },
      playVideo() {
        this.played = true;
      },
    };
    suite.context.player = player;
    suite.context.isPlayerReady = true;
    suite.timestampLink.attributes['data-time'] = '1:02:03';
    suite.timestampLink.click();
    assert.deepEqual(player.seekCalls, [{ seconds: 3723, allowSeekAhead: true }]);
    assert.equal(player.played, true);
  }],
  ['timestamp click scrolls to top on mobile width', () => {
    const suite = createDefaultSuite({ innerWidth: 400 });
    suite.context.player = { seekTo() {}, playVideo() {} };
    suite.context.isPlayerReady = true;
    suite.timestampLink.click();
    assert.equal(suite.scrollCalls.length, 1);
    assert.equal(suite.scrollCalls[0].top, 0);
  }],
  ['timestamp click does not scroll on desktop width', () => {
    const suite = createDefaultSuite({ innerWidth: 1200 });
    suite.context.player = { seekTo() {}, playVideo() {} };
    suite.context.isPlayerReady = true;
    suite.timestampLink.click();
    assert.equal(suite.scrollCalls.length, 0);
  }],
  ['timestamp click with malformed player fails closed', () => {
    const suite = createDefaultSuite();
    suite.context.player = { seekTo() {} };
    suite.context.isPlayerReady = true;
    suite.timestampLink.click();
    assert.equal(suite.alerts.pop(), 'The YouTube video is still connecting. Please wait one second and try again.');
  }],
  ['menu open and close handlers work', () => {
    const suite = createDefaultSuite();
    suite.menuBtn.onclick();
    assert.equal(suite.menuModal.style.display, 'flex');
    suite.closeModal.onclick();
    assert.equal(suite.menuModal.style.display, 'none');
  }],
  ['menu closes when clicking backdrop', () => {
    const suite = createDefaultSuite();
    suite.menuModal.style.display = 'flex';
    suite.window.onclick({ target: suite.menuModal });
    assert.equal(suite.menuModal.style.display, 'none');
  }],
  ['settings tab shows block display', () => {
    const suite = createDefaultSuite();
    suite.tabButtons[1].onclick();
    assert.equal(suite.tabContents[1].style.display, 'block');
  }],
  ['chat tab shows flex display', () => {
    const suite = createDefaultSuite();
    suite.tabButtons[2].onclick();
    assert.equal(suite.tabContents[2].style.display, 'flex');
  }],
  ['saved theme dark applies at startup', () => {
    const suite = createDefaultSuite();
    assert.equal(suite.bodyClassList.contains('dark-theme'), true);
  }],
  ['theme toggle persists updated value', () => {
    const suite = createDefaultSuite();
    suite.themeToggle.checked = false;
    suite.themeToggle.dispatchEvent({ type: 'change' });
    assert.equal(suite.bodyClassList.contains('dark-theme'), false);
    assert.equal(suite.storedValues.get('rhino-cheat-sheet-theme'), 'light');
  }],
  ['saved video width clamps to max', () => {
    const suite = createDefaultSuite();
    assert.equal(suite.videoSizeSlider.value, '80');
    assert.equal(suite.cssVars['--video-width'], '80%');
  }],
  ['saved video width defaults when storage value invalid', () => {
    const suite = createDefaultSuite({
      localStorage: {
        'rhino-cheat-sheet-video-width': 'not-a-number',
      },
    });
    assert.equal(suite.videoSizeSlider.value, '70');
  }],
  ['video slider clamps low and rounds on input', () => {
    const suite = createDefaultSuite();
    suite.videoSizeSlider.value = '39.4';
    suite.videoSizeSlider.dispatchEvent({ type: 'input' });
    assert.equal(suite.videoSizeSlider.value, '40');
    assert.equal(suite.cssVars['--video-width'], '40%');
  }],
  ['video slider rounds to nearest integer on input', () => {
    const suite = createDefaultSuite();
    suite.videoSizeSlider.value = '55.6';
    suite.videoSizeSlider.dispatchEvent({ type: 'input' });
    assert.equal(suite.videoSizeSlider.value, '56');
    assert.equal(suite.storedValues.get('rhino-cheat-sheet-video-width'), '56');
  }],
  ['storage exceptions do not crash startup', () => {
    const suite = createDefaultSuite({ throwOnStorage: true, systemPrefersDark: true });
    assert.equal(suite.videoSizeSlider.value, '70');
    assert.equal(suite.bodyClassList.contains('dark-theme'), true);
  }],
  ['print button calls window.print', () => {
    const suite = createDefaultSuite();
    suite.printPageBtn.click();
    assert.equal(suite.printCalls.length, 1);
  }],
  ['download button creates blob URL and revokes it', () => {
    const suite = createDefaultSuite();
    suite.downloadHtmlBtn.click();
    assert.equal(suite.blobParts.length, 1);
    assert.equal(suite.createdUrls.length, 1);
    assert.equal(suite.revokedUrls.length, 1);
    const anchor = suite.createdAnchors[0];
    assert.ok(anchor);
    assert.equal(anchor.clickCount, 1);
  }],
  ['download button fallback alerts when blob creation fails', () => {
    const suite = createDefaultSuite({ throwOnBlob: true });
    suite.downloadHtmlBtn.click();
    assert.equal(suite.alerts.pop(), 'Download is not supported in this browser. You can still use Print / Save PDF.');
  }],
  ['chat rendering escapes HTML and keeps formatting tokens', () => {
    const suite = createDefaultSuite();
    suite.context.appendMessage('<img src=x onerror=alert(1)> **bold** `code` *italic*', true);
    const rendered = suite.chatBox.children.at(-1).innerHTML;
    assert.match(rendered, /&lt;img src=x onerror=alert\(1\)&gt;/);
    assert.match(rendered, /<strong>bold<\/strong>/);
    assert.match(rendered, /<code>code<\/code>/);
    assert.match(rendered, /<em>italic<\/em>/);
  }],
  ['chat rendering auto-links https urls', () => {
    const suite = createDefaultSuite();
    suite.context.appendMessage('Open https://example.com/docs for help', false);
    const rendered = suite.chatBox.children.at(-1).innerHTML;
    assert.match(rendered, /<a href="https:\/\/example\.com\/docs" target="_blank" rel="noopener noreferrer">https:\/\/example\.com\/docs<\/a>/);
  }],
  ['chat rendering does not auto-link javascript scheme', () => {
    const suite = createDefaultSuite();
    suite.context.appendMessage('javascript:alert(1)', false);
    const rendered = suite.chatBox.children.at(-1).innerHTML;
    assert.doesNotMatch(rendered, /<a href="javascript:alert\(1\)"/);
  }],
  ['blank chat input is ignored', () => {
    const suite = createDefaultSuite();
    suite.chatInput.value = '   ';
    suite.context.handleSend();
    assert.equal(suite.chatBox.children.length, 0);
  }],
  ['regular chat send queues bot response timer', () => {
    const suite = createDefaultSuite();
    suite.chatInput.value = 'hello there';
    suite.context.handleSend();
    assert.equal(suite.chatBox.children.length, 1);
    assert.ok(suite.timers.some((item) => item.delay === 1000));
  }],
  ['enter key sends chat and prevents default submit', () => {
    const suite = createDefaultSuite();
    let prevented = false;
    suite.chatInput.value = 'send by enter';
    suite.chatInput.dispatchEvent({
      type: 'keydown',
      key: 'Enter',
      shiftKey: false,
      preventDefault() {
        prevented = true;
      },
    });
    assert.equal(prevented, true);
    assert.equal(suite.chatBox.children.length, 1);
  }],
  ['shift+enter does not send chat', () => {
    const suite = createDefaultSuite();
    let prevented = false;
    suite.chatInput.value = 'line break';
    suite.chatInput.dispatchEvent({
      type: 'keydown',
      key: 'Enter',
      shiftKey: true,
      preventDefault() {
        prevented = true;
      },
    });
    assert.equal(prevented, false);
    assert.equal(suite.chatBox.children.length, 0);
  }],
  ['host command returns links in chat without opening popup', () => {
    const suite = createDefaultSuite();
    suite.chatInput.value = 'host';
    suite.context.handleSend();
    assert.notEqual(suite.videoErrorModal.style.display, 'flex');
    const lastMessage = suite.chatBox.children.at(-1).innerHTML;
    assert.match(lastMessage, /Congrats, you found the hidden command/);
    assert.match(lastMessage, /You can find the manual here/);
    assert.match(lastMessage, /hosted version of the website via ngrok/);
    assert.match(lastMessage, /<a href="https:\/\/extraterritorial-carlota-ironfisted\.ngrok-free\.dev\/Rhino%208%20Interactive%20Cheat%20Sheet%20Manual\.pdf"[^>]*>Manual PDF<\/a>/);
    assert.match(lastMessage, /<a href="https:\/\/extraterritorial-carlota-ironfisted\.ngrok-free\.dev\/Rhino8_cheat_sheet_timestamps_interactive\.html"[^>]*>Rhino8_cheat_sheet_timestamps_interactive<\/a>/);
  }],
  ['/host command also returns links without popup', () => {
    const suite = createDefaultSuite();
    suite.chatInput.value = '/host';
    suite.context.handleSend();
    assert.notEqual(suite.videoErrorModal.style.display, 'flex');
    const lastMessage = suite.chatBox.children.at(-1).innerHTML;
    assert.match(lastMessage, /Congrats, you found the hidden command/);
  }],
  ['host popup command explicitly opens helper popup', () => {
    const suite = createDefaultSuite();
    suite.chatInput.value = 'host popup';
    suite.context.handleSend();
    assert.equal(suite.videoErrorModal.style.display, 'flex');
  }],
  ['/host popup command is also supported', () => {
    const suite = createDefaultSuite();
    suite.chatInput.value = '/host popup';
    suite.context.handleSend();
    assert.equal(suite.videoErrorModal.style.display, 'flex');
  }],
  ['showVideoErrorPopup toggles aria visible', () => {
    const suite = createDefaultSuite();
    suite.context.showVideoErrorPopup();
    assert.equal(suite.videoErrorModal.style.display, 'flex');
    assert.equal(suite.videoErrorModal.getAttribute('aria-hidden'), 'false');
  }],
  ['video error popup uses red hotkeys button label', () => {
    const suite = createDefaultSuite();
    assert.equal(suite.closeVideoErrorModal.textContent, 'Okay, I just want to see hotkeys');
    assert.equal(suite.closeVideoErrorModal.classList.contains('hotkeys-btn'), true);
  }],
  ['first popup show does not hide video layout yet', () => {
    const suite = createDefaultSuite({ locationProtocol: 'file:' });
    suite.context.showVideoErrorPopup();
    assert.equal(suite.bodyClassList.contains('video-unavailable-layout'), false);
  }],
  ['hideVideoErrorPopup toggles aria hidden', () => {
    const suite = createDefaultSuite();
    suite.context.showVideoErrorPopup();
    suite.context.hideVideoErrorPopup();
    assert.equal(suite.videoErrorModal.style.display, 'none');
    assert.equal(suite.videoErrorModal.getAttribute('aria-hidden'), 'true');
  }],
  ['dismissed popup activates fallback layout for local unresolved video', () => {
    const suite = createDefaultSuite({ locationProtocol: 'file:' });
    suite.context.showVideoErrorPopup();
    suite.context.hideVideoErrorPopup();
    assert.equal(suite.bodyClassList.contains('video-unavailable-layout'), true);
  }],
  ['dismissed popup activates fallback layout after automatic startup failure', () => {
    const suite = createDefaultSuite();
    suite.context.isPlayerReady = false;
    runTimerByDelay(suite, 3500);
    suite.context.hideVideoErrorPopup();
    assert.equal(suite.bodyClassList.contains('video-unavailable-layout'), true);
  }],
  ['dismissed popup does not activate fallback layout when player is ready', () => {
    const suite = createDefaultSuite();
    suite.context.isPlayerReady = true;
    suite.context.showVideoErrorPopup();
    suite.context.hideVideoErrorPopup();
    assert.equal(suite.bodyClassList.contains('video-unavailable-layout'), false);
  }],
  ['fallback layout remains active on subsequent popup reopen', () => {
    const suite = createDefaultSuite({ locationProtocol: 'file:' });
    suite.context.showVideoErrorPopup();
    suite.context.hideVideoErrorPopup();
    suite.context.showVideoErrorPopup();
    assert.equal(suite.bodyClassList.contains('video-unavailable-layout'), true);
    assert.equal(suite.context.videoErrorPopupShownCount >= 2, true);
  }],
  ['video error modal close button hides popup', () => {
    const suite = createDefaultSuite();
    suite.context.showVideoErrorPopup();
    suite.closeVideoErrorModal.click();
    assert.equal(suite.videoErrorModal.style.display, 'none');
  }],
  ['hotkeys button hides popup and can trigger fallback layout after dismissal', () => {
    const suite = createDefaultSuite({ locationProtocol: 'file:' });
    suite.context.showVideoErrorPopup();
    suite.closeVideoErrorModal.click();
    assert.equal(suite.videoErrorModal.style.display, 'none');
    assert.equal(suite.bodyClassList.contains('video-unavailable-layout'), true);
  }],
  ['video error modal backdrop click hides popup', () => {
    const suite = createDefaultSuite();
    suite.context.showVideoErrorPopup();
    suite.videoErrorModal.dispatchEvent({ type: 'click', target: suite.videoErrorModal });
    assert.equal(suite.videoErrorModal.style.display, 'none');
  }],
  ['video error modal inner click does not close popup', () => {
    const suite = createDefaultSuite();
    const inner = createMockElement('inner');
    suite.context.showVideoErrorPopup();
    suite.videoErrorModal.dispatchEvent({ type: 'click', target: inner });
    assert.equal(suite.videoErrorModal.style.display, 'flex');
  }],
];

for (const [name, fn] of tests) {
  const ok = runTest(name, fn);
  if (ok) {
    passed += 1;
  } else {
    failed += 1;
  }
}

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exitCode = 1;
}
