/* REFERENCE ONLY: anonymized example; never install into a real browser. */
/* ICBU receive-only transport. No selectors, Alibaba requests, cookies or sending actions. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ICBUPWACore = factory();
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const ENDPOINT = (globalThis.ICBU_EXAMPLE_CONFIG?.inboundUrl || "http://gateway.invalid:8791/api/inbound/alibaba");
  const PREFIX = "icbu.example.history.v1.event.";
  const RECEIPT_PREFIX = "icbu.example.history.v1.receipt.";
  const ROLES = new Set(["buyer", "seller", "system", "unknown"]);

  function plain(value) { return JSON.parse(JSON.stringify(value)); }
  function failure(code) { const err = new Error(code); err.code = code; return err; }
  function string(value, max, optional) {
    if (value == null && optional) return null;
    if (typeof value !== "string") throw failure("invalid_field_type");
    if (value.length > max || value.includes("\u0000")) throw failure("field_too_long_or_invalid");
    return value.trim() || (optional ? null : "");
  }
  function safeUrl(raw) {
    const u = new URL(raw);
    const allowed=(u.hostname==="history.fixture.invalid"&&u.pathname==="/message/maDetail.htm")
      ||(u.hostname==="messenger.fixture.invalid"&&u.pathname==="/message/weblitePWA.htm");
    if (u.protocol !== "https:" || !allowed
        || u.username || u.password || u.port) throw failure("invalid_source_url");
    return u.origin + u.pathname; // Never export URL tokens, query strings or hashes.
  }
  function normalize(raw, uuid, now) {
    if (!raw || raw.account && raw.account !== "Example") throw failure("account_mismatch");
    const content = string(raw.content, 16000, false);
    if (!content) throw failure("empty_content");
    const role = raw.role == null ? "unknown" : raw.role;
    if (!ROLES.has(role)) throw failure("invalid_role");
    if (raw.history !== true) ICBUPWAFreshGate.proof(raw);
    if (raw.conversation_id == null || raw.buyer_name == null) throw failure("selector_missing_identity");
    const conversationId = string(raw.conversation_id, 256, false);
    const buyerName = string(raw.buyer_name, 200, false);
    const sourceUrl=safeUrl(raw.url);
    const cidMatches=sourceUrl.startsWith("https://messenger.fixture.invalid/")
      ? /^tm:[A-Za-z0-9_-]{1,128}$/.test(conversationId) : /^\d{1,30}$/.test(conversationId);
    if (!cidMatches || !buyerName) throw failure("selector_missing_identity");
    if(sourceUrl.startsWith("https://messenger.fixture.invalid/")
        &&(typeof raw.message_id!=="string"||!/^\d{1,30}(?:\.PNM)?$/.test(raw.message_id)))
      throw failure("tm_message_id_invalid");
    const eventId = raw.event_id || uuid();
    if (typeof eventId !== "string" || !/^[A-Za-z0-9:_-]{1,128}$/.test(eventId)) {
      throw failure("invalid_event_id");
    }
    const timestamp = raw.observed_at || new Date(now()).toISOString();
    if (typeof timestamp !== "string" || !Number.isFinite(Date.parse(timestamp))) {
      throw failure("invalid_observed_at");
    }
    const out = {
      account: "Example",
      conversation_id: conversationId,
      buyer_name: buyerName,
      country: raw.country == null ? "" : string(raw.country, 100, false),
      content,
      url: sourceUrl,
      message_id: string(raw.message_id, 256, true),
      event_id: eventId,
      history: raw.history,
      role,
      observed_at: timestamp
    };
    if (raw.sent_at != null) {
      if (typeof raw.sent_at !== "string" || !Number.isFinite(Date.parse(raw.sent_at))) throw failure("invalid_sent_at");
      out.sent_at = raw.sent_at;
    }
    if(raw.history===false){out.probe_version=ICBUPWAFreshGate.VERSION;out.live_provenance=ICBUPWAFreshGate.proof(raw);}
    return out; // Deliberate allowlist: all other input fields are discarded.
  }
  function createGMStorage(gm) {
    return {
      async keys() { return (await gm.list()).filter(k => k.startsWith(PREFIX)); },
      async get(key) { return await gm.get(key, null); },
      async put(key, value) { await gm.set(key, plain(value)); },
      async remove(key) { await gm.remove(key); }
    };
  }
  function createConsoleStorage(localStorage) {
    return {
      async keys() {
        const keys = [];
        for (let i = 0; i < localStorage.length; i++) {
          const key = localStorage.key(i); if (key && key.startsWith(PREFIX)) keys.push(key);
        }
        return keys;
      },
      async get(key) { const raw = localStorage.getItem(key); return raw == null ? null : JSON.parse(raw); },
      async put(key, value) { localStorage.setItem(key, JSON.stringify(value)); },
      async remove(key) { localStorage.removeItem(key); }
    };
  }
  function createGMTransport(gmRequest, timeoutMs = 12000) {
    return function post(body, token) {
      return new Promise((resolve, reject) => {
        let finished = false, handle;
        const finish = (fn, value) => { if (!finished) { finished = true; clearTimeout(timer); fn(value); } };
        // anonymous forces fetch mode in modern TM, where its timeout option may not work.
        const timer = setTimeout(() => {
          finish(reject, failure("transport_timeout")); if (handle) handle.abort();
        }, timeoutMs);
        try {
          handle = gmRequest({
            method: "POST", url: ENDPOINT,
            headers: {"Content-Type": "application/json", "Authorization": "Bearer " + token,
              "X-Idempotency-Key": body.event_id},
            data: JSON.stringify(body), anonymous: true, redirect: "error",
            onload(response) {
              if (response.finalUrl && response.finalUrl !== ENDPOINT) {
                finish(reject, failure("unexpected_redirect")); return;
              }
              let payload = null;
              try { payload = JSON.parse(response.responseText); } catch (_) { /* ACK validation fails closed */ }
              finish(resolve, {status: response.status, payload});
            },
            onerror() { finish(reject, failure("transport_network_error")); },
            ontimeout() { finish(reject, failure("transport_timeout")); },
            onabort() { finish(reject, failure("transport_aborted")); }
          });
        } catch (_) { finish(reject, failure("transport_start_failed")); }
      });
    };
  }
  function createFetchTransport(fetchFn, timeoutMs = 12000) {
    return async function post(body, token) {
      const abort = new AbortController();
      const timer = setTimeout(() => abort.abort(), timeoutMs);
      try {
        const r = await fetchFn(ENDPOINT, {
          method: "POST", mode: "cors", credentials: "omit", redirect: "error", cache: "no-store",
          headers: {"Content-Type": "application/json", "Authorization": "Bearer " + token,
            "X-Idempotency-Key": body.event_id},
          body: JSON.stringify(body), signal: abort.signal
        });
        if (r.type === "opaque" || r.status === 0) throw failure("opaque_response_not_ack");
        let payload = null;
        try { payload = await r.json(); } catch (_) { /* no readable ACK */ }
        return {status: r.status, payload};
      } catch (error) {
        if (error && error.code) throw error;
        throw failure("fetch_failed_cors_lna_mixedcontent_or_network");
      } finally { clearTimeout(timer); }
    };
  }
  function createProbe(options) {
    const {storage, post, getToken} = options;
    const now = options.now || Date.now;
    const uuid = options.uuid || (() => crypto.randomUUID());
    const maxEvents = options.maxEvents || 500;
    const maxBytes = options.maxBytes || 2 * 1024 * 1024;
    let busy = false, timer = null, paused = false, lastError = null, lastAck = null;
    let enqueueChain = Promise.resolve();
    const digest=async value=>[...new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(JSON.stringify(value))))]
      .map(n=>n.toString(16).padStart(2,"0")).join("");
    const receipt=async id=>await storage.get(RECEIPT_PREFIX+id);
    const emit = () => { if (options.onStatus) options.onStatus({paused, lastError, lastAck, busy}); };
    async function records() {
      const result = [];
      for (const key of await storage.keys()) {
        const record = await storage.get(key);
        if (!record) continue;
        if (!record.event || !record.event.event_id || typeof record.attempts !== "number") {
          throw failure("spool_corrupt_do_not_delete");
        }
        result.push({key, ...record});
      }
      return result.sort((a,b) => a.queued_at - b.queued_at);
    }
    function enqueue(raw) {
      const task = enqueueChain.then(async () => {
        const event = normalize(raw, uuid, now);
        const accepted=await receipt(event.event_id);
        if(accepted) {
          if(accepted.persisted!==true||accepted.event_id!==event.event_id||accepted.event_digest!==await digest(event))
            throw failure("receipt_payload_conflict");
          return {queued:false,acknowledged:true,event_id:event.event_id};
        }
        const queued = await records();
        const same = queued.find(r => r.event.event_id === event.event_id);
        if (same) {
          if (raw.observed_at == null) event.observed_at = same.event.observed_at;
          if (JSON.stringify(same.event) !== JSON.stringify(event)) throw failure("event_id_payload_conflict");
          return {queued: true, duplicate: true, event_id: event.event_id};
        }
        // Unknown platform IDs are never deduplicated by text or nickname.
        const bytes = queued.reduce((n,r) => n + new TextEncoder().encode(JSON.stringify(r.event)).length, 0);
        if (queued.length >= maxEvents || bytes + new TextEncoder().encode(JSON.stringify(event)).length > maxBytes) {
          lastError = "spool_full_capture_must_pause"; emit(); throw failure(lastError);
        }
        await storage.put(PREFIX + event.event_id, {
          event, queued_at: now(), attempts: 0, next_attempt_at: 0, blocked: false
        });
        return {queued: true, duplicate: false, event_id: event.event_id};
      });
      enqueueChain = task.catch(() => {});
      return task;
    }
    async function flush() {
      if (busy || paused) return {busy, paused};
      busy = true; emit();
      try {
        const token = await getToken();
        if (typeof token !== "string" || !token.trim() || /[\r\n]/.test(token)) {
          lastError = "missing_or_invalid_probe_token"; emit(); return;
        }
        for (const item of await records()) {
          if (paused || item.blocked || item.next_attempt_at > now()) continue;
          // The new prefix must never contain old live work or an incompatible source.
          if(item.event.history!==true) ICBUPWAFreshGate.proof(item.event);
          const accepted=await receipt(item.event.event_id);
          if(accepted) {
            if(accepted.persisted!==true||accepted.event_digest!==await digest(item.event)) throw failure("receipt_payload_conflict");
            await storage.remove(item.key);continue;
          }
          if(options.shouldSend&&!options.shouldSend(item.event))continue;
          if(options.beforePost) await options.beforePost(item.event);
          if(paused) break;
          let outcome;
          try { outcome = await post(plain(item.event), token); }
          catch (error) { outcome = {status: 0, error: error.code || "transport_error"}; }
          const ack = outcome.payload;
          if (outcome.status >= 200 && outcome.status < 300 && ack && ack.ok === true
              && ack.persisted === true && ack.event_id === item.event.event_id) {
            const durable={event_id:item.event.event_id,inbound_id:ack.inbound_id||null,
              at:new Date(now()).toISOString(),persisted:true,event_digest:await digest(item.event)};
            // A failed receipt write leaves the original event untouched for the same-ID retry.
            await storage.put(RECEIPT_PREFIX+item.event.event_id,durable);
            if(options.onDurableAck) await options.onDurableAck(durable);
            await storage.remove(item.key);
            lastAck = durable;
            lastError = null; emit(); continue;
          }
          const permanent = outcome.status >= 400 && outcome.status < 500 && ![408,429].includes(outcome.status);
          const authError = [401,403].includes(outcome.status);
          const record = {
            event: item.event, queued_at: item.queued_at, attempts: item.attempts + 1,
            next_attempt_at: now() + Math.min(60000, 1000 * (2 ** Math.min(item.attempts, 6))),
            blocked: permanent && !authError,
            last_error: outcome.error || (outcome.status >= 200 && outcome.status < 300
              ? "durable_ack_missing_or_mismatched" : "http_" + outcome.status)
          };
          await storage.put(item.key, record); lastError = record.last_error;
          if (authError) paused = true;
          emit();
          if (!permanent || authError) break; // Bounded backoff; never a tight retry loop.
        }
      } catch (error) {
        lastError = error.code || "storage_or_transport_failure"; emit();
      } finally { busy = false; emit(); }
    }
    async function status() {
      const list = await records();
      return {account: "Example", endpoint: ENDPOINT, queued: list.length,
        blocked: list.filter(r => r.blocked).length, paused, busy, lastError, lastAck};
    }
    function start(intervalMs = 5000) {
      if (!timer) timer = setInterval(() => { void flush(); }, Math.max(1000, intervalMs));
      paused = false; void flush();
    }
    function stop() { if (timer) clearInterval(timer); timer = null; paused = true; emit(); }
    function resume(send=true) { paused = false; if(send) void flush(); }
    async function drain(eventIds,{timeoutMs=180000,active=()=>true,sleep=ms=>new Promise(r=>setTimeout(r,ms))}={}) {
      await enqueueChain;
      const ids=[...new Set(eventIds)],deadline=now()+timeoutMs;
      for(;;) {
        if(!active()) throw failure("capture_paused");
        const waiting=[];
        for(const id of ids) {
          const ack=await receipt(id);
          if(!(ack&&ack.persisted===true&&ack.event_id===id)) waiting.push(id);
        }
        if(!waiting.length) return {persisted:true,event_ids:ids};
        const queued=await records();
        if(waiting.some(id=>!queued.some(r=>r.event.event_id===id))) throw failure("checkpoint_event_not_queued");
        if(waiting.some(id=>queued.some(r=>r.event.event_id===id&&r.blocked))) throw failure("blocked_event_requires_review");
        if(paused) throw failure("transport_paused");
        if(now()>=deadline) throw failure("drain_timeout_events_preserved");
        await flush();
        if(lastError==="missing_or_invalid_probe_token"||paused) throw failure(lastError||"transport_paused");
        await sleep(250);
      }
    }
    async function eventState(id){
      const ack=await receipt(id),queued=await storage.get(PREFIX+id);
      return {queued:!!queued,blocked:!!(queued&&queued.blocked),persisted:!!(ack&&ack.persisted===true&&ack.event_id===id)};
    }
    return Object.freeze({enqueue, flush, status, start, stop, resume,drain,receipt,eventState});
  }
  return Object.freeze({ENDPOINT, PREFIX, RECEIPT_PREFIX, createProbe, createGMStorage, createConsoleStorage,
    createGMTransport, createFetchTransport, normalize});
});
