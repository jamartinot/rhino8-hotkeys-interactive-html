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
      this.children.push(child);
      this.scrollHeight = this.children.length;
      return child;
    },
    getAttribute(name) {
      return this.attributes ? this.attributes[name] : undefined;
    },
    setAttribute(name, value) {
      this.attributes = this.attributes || {};
      this.attributes[name] = value;
    },
  };
}

function createTimestampLink(timeValue) {
  const link = createMockElement('timestamp');
  link.attributes = { 'data-time': timeValue };
  link.preventDefaultCalled = false;
  link.addEventListener = function(type, handler) {
    this.listeners.set(type, handler);
  };
  link.click = function() {
    const handler = this.listeners.get('click');
    if (!handler) {
      throw new Error('Timestamp click handler was not attached.');
    }
    const event = {
      type: 'click',
      preventDefault: () => {
        link.preventDefaultCalled = true;
      },
    };
    handler.call(link, event);
  };
  return link;
}

function createContext(options = {}) {
  const htmlSource = fs.readFileSync(htmlPath, 'utf8');
  const scriptSource = extractInlineScript(htmlSource);

  const timers = [];
  const alerts = [];
  const cssVars = {};
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
  const chatBox = createMockElement('chatBox');
  const chatInput = createMockElement('chatInput');
  const sendBtn = createMockElement('sendBtn');
  const tabButtons = ['tab-info', 'tab-settings', 'tab-chat'].map((tabId, index) => {
    const button = createMockElement(`tab-btn-${index}`);
    button.attributes = { 'data-tab': tabId };
    return button;
  });
  const tabContents = ['tab-info', 'tab-settings', 'tab-chat'].map((tabId) => {
    const content = createMockElement(tabId);
    content.id = tabId;
    return content;
  });
  const bodyClassList = createClassList();

  const elementMap = {
    menuModal,
    menuBtn,
    closeModal,
    themeToggle,
    videoSizeSlider,
    chatBox,
    chatInput,
    sendBtn,
    tabInfo: tabContents[0],
    tabSettings: tabContents[1],
    tabChat: tabContents[2],
    player: createMockElement('player'),
  };

  const document = {
    body: {
      classList: bodyClassList,
    },
    documentElement: {
      style: {
        setProperty(name, value) {
          cssVars[name] = value;
        },
      },
    },
    createElement(tagName) {
      if (tagName !== 'script') {
        return createMockElement(tagName);
      }
      return {
        tagName,
        src: '',
      };
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
      if (selector === '.tab-btn') {
        return tabButtons;
      }
      if (selector === '.tab-content') {
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

  const windowObject = {
    innerWidth: options.innerWidth ?? 1280,
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
    setTimeout(handler, delay) {
      timers.push({ handler, delay });
      return timers.length;
    },
    clearTimeout() {},
    YT: {
      Player(playerId, config) {
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
    storedValues,
    timestampLink,
    menuModal,
    menuBtn,
    closeModal,
    themeToggle,
    videoSizeSlider,
    chatBox,
    chatInput,
    sendBtn,
    tabButtons,
    tabContents,
    bodyClassList,
  };
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

const suite = createContext({
  localStorage: {
    'rhino-cheat-sheet-theme': 'dark',
    'rhino-cheat-sheet-video-width': '999',
  },
  systemPrefersDark: false,
  innerWidth: 1280,
  timestampTime: '02:34',
});

let passed = 0;
let failed = 0;

const tests = [
  ['parseTimeStr accepts valid formats and rejects malformed input', () => {
    assert.equal(suite.context.parseTimeStr('02:34'), 154);
    assert.equal(suite.context.parseTimeStr('1:23:37'), 5017);
    assert.equal(suite.context.parseTimeStr('bad-input'), 0);
    assert.equal(suite.context.parseTimeStr('1:2:3:4'), 0);
    assert.equal(suite.context.parseTimeStr(''), 0);
    assert.doesNotThrow(() => suite.context.parseTimeStr(null));
    assert.equal(suite.context.parseTimeStr(null), 0);
  }],
  ['timestamp clicks do not execute before the player is ready', () => {
    suite.context.isPlayerReady = false;
    suite.timestampLink.click();
    assert.equal(suite.alerts.pop(), 'The YouTube video is still connecting. Please wait one second and try again.');
    assert.equal(suite.scrollCalls.length, 0);
  }],
  ['timestamp clicks seek and play when the player is ready', () => {
    const player = {
      seekTo(seconds, allowSeekAhead) {
        this.seekCalls = this.seekCalls || [];
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
  ['timestamp clicks fail closed when the player is malformed', () => {
    suite.context.player = { seekTo() {} };
    suite.context.isPlayerReady = true;
    suite.timestampLink.attributes['data-time'] = '02:34';
    assert.doesNotThrow(() => suite.timestampLink.click());
    assert.equal(suite.alerts.pop(), 'The YouTube video is still connecting. Please wait one second and try again.');
  }],
  ['chat rendering escapes HTML and preserves limited formatting', () => {
    suite.chatBox.children.length = 0;
    suite.context.appendMessage('<img src=x onerror=alert(1)> **bold** `code` *italic*', true);
    const rendered = suite.chatBox.children.at(-1).innerHTML;
    assert.match(rendered, /&lt;img src=x onerror=alert\(1\)&gt;/);
    assert.match(rendered, /<strong>bold<\/strong>/);
    assert.match(rendered, /<code>code<\/code>/);
    assert.match(rendered, /<em>italic<\/em>/);
  }],
  ['blank chat input is ignored and long content stays inert', () => {
    suite.chatBox.children.length = 0;
    suite.chatInput.value = '   ';
    suite.context.handleSend();
    assert.equal(suite.chatBox.children.length, 0);

    const largePayload = '<script>'.repeat(2000);
    suite.chatInput.value = largePayload;
    suite.context.handleSend();
    assert.equal(suite.chatBox.children.length, 1);
    assert.match(suite.chatBox.children[0].innerHTML, /&lt;script&gt;/);
  }],
  ['saved video width is constrained to the slider range', () => {
    assert.equal(suite.videoSizeSlider.value, '80');
    assert.equal(suite.cssVars['--video-width'], '80%');
  }],
  ['theme persistence prefers explicit saved state', () => {
    assert.equal(suite.bodyClassList.contains('dark-theme'), true);
    suite.themeToggle.checked = false;
    suite.themeToggle.dispatchEvent({ type: 'change' });
    assert.equal(suite.bodyClassList.contains('dark-theme'), false);
    assert.equal(suite.storedValues.get('rhino-cheat-sheet-theme'), 'light');
  }],
  ['menu and tabs can be opened and closed without leaking state', () => {
    suite.menuBtn.onclick();
    assert.equal(suite.menuModal.style.display, 'flex');
    suite.closeModal.onclick();
    assert.equal(suite.menuModal.style.display, 'none');

    const settingsButton = suite.tabButtons[1];
    settingsButton.onclick();
    assert.equal(settingsButton.classList.contains('active'), true);
    assert.equal(suite.tabContents[1].style.display, 'block');

    const chatButton = suite.tabButtons[2];
    chatButton.onclick();
    assert.equal(chatButton.classList.contains('active'), true);
    assert.equal(suite.tabContents[2].style.display, 'flex');
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
