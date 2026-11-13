'use strict';

(() => {
  const defaults = {master:1,combat:.95,spawn:1,skill:.9,battle:.95,gift:.85,ambient:.35};
  const profiles = {
    cat:['spawn',.065,1,[[520,.08,'square',.05,780]]],
    enemy:['spawn',.065,1,[[250,.11,'triangle',.085,390]]],
    boss:['spawn',.24,3,[[95,.2,'sawtooth',.08,155],[196,.16,'triangle',.10,294,.1]]],
    hit:['combat',.12,0,[[180,.06,'square',.05,90]]],
    ranged:['combat',.15,0,[[510,.075,'triangle',.11,210]]],
    heavy:['combat',.22,2,[[115,.14,'triangle',.17,49],[0,.12,'noise',.09]]],
    bossHit:['combat',.25,2,[[85,.21,'square',.10,39],[0,.16,'noise',.12]]],
    baseHit:['combat',.24,1,[[135,.13,'triangle',.15,73],[0,.09,'noise',.06]]],
    death:['combat',.14,1,[[300,.12,'triangle',.09,90]]],
    cannonCat:['skill',.45,3,[[90,.6,'sawtooth',.13,40],[0,.5,'noise',.20]]],
    cannonEnemy:['skill',.45,3,[[145,.52,'square',.09,38],[0,.47,'noise',.18]]],
    power:['skill',.4,2,[[440,.13,'triangle',.11,720],[660,.13,'triangle',.10,1020,.11]]],
    battleStart:['battle',.4,3,[[330,.11,'square',.07],[495,.16,'triangle',.14,null,.12]]],
    map:['battle',.4,3,[[260,.24,'triangle',.10,780]]],
    collapse:['battle',.4,5,[[96,.56,'triangle',.27,32],[0,.58,'noise',.29]]],
    winCat:['battle',.7,4,[523,659,784,1046,784,1046].map((f,i)=>[f,.16,'square',.085,null,.45+i*.13])],
    winEnemy:['battle',.7,4,[392,494,587,784,587,784].map((f,i)=>[f,.17,'triangle',.15,null,.45+i*.13])],
    draw:['battle',.7,4,[[330,.22,'triangle',.14,247,.45]]],
    gift:['gift',.5,2,[[740,.09,'triangle',.10,990],[1175,.11,'triangle',.08,null,.1]]],
    finisherCue:['gift',.5,5,[[220,.27,'triangle',.19,440],[440,.4,'square',.10,880,.25]]],
    finisherLaunch:['skill',.4,5,[[155,.44,'sawtooth',.14,61],[0,.38,'noise',.15]]],
    finisherImpact:['skill',.4,5,[[0,.43,'noise',.27],[68,.44,'triangle',.24,32]]],
    ready:['battle',.3,3,[[523,.12,'triangle',.19],[784,.16,'triangle',.18,null,.11]]],
  };
  const maxVoices = 16;
  const button = document.getElementById('live-audio-start');
  const setup = new URLSearchParams(location.search).get('audio') === 'setup';
  const test = new URLSearchParams(location.search).has('audio-test');

  class AudioManager {
    constructor() {
      this.enabled = true;
      this.volume = {...defaults};
      this.groups = {};
      this.voices = new Set();
      this.last = new Map();
      this.ducked = false;
      this.peakVoices = 0;
      this.created = 0;
      if (test) this.events = {};
      button.addEventListener('click', () => this.unlock(true));
      window.addEventListener('focus', () => {
        if (this.started) this.unlock();
      });
      // OBS Interact can also send A to the actual clean capture document.
      window.addEventListener('keydown', event => {
        if (event.isTrusted && !event.repeat && !event.ctrlKey && !event.altKey && !event.metaKey &&
            event.key.toLowerCase() === 'a') this.unlock(true);
      });
      document.addEventListener('visibilitychange', () => {
        if (!document.hidden && this.started) this.unlock();
      });
    }

    configure(enabled, levels) {
      this.enabled = enabled;
      this.volume = {...defaults, ...levels};
      this.mix();
      if (enabled) this.unlock();
      this.report();
    }

    report() {
      const state = !this.enabled ? 'disabled' : this.unavailable ? 'unavailable' : this.context?.state || 'suspended';
      if (state === this.state) return;
      this.state = state;
      button.hidden = !setup || !this.enabled || state === 'running' || state === 'unavailable' || state === 'closed';
      if (state === 'suspended' || state === 'interrupted') {
        console.warn('Pixel Meow audio: bấm A trong nguồn game hoặc mở nguồn với ?audio=setup.');
      } else if (state === 'closed') {
        console.warn('Pixel Meow audio: nguồn âm thanh đã đóng. Tải lại nguồn và kích hoạt lại.');
      }
      window.dispatchEvent(new CustomEvent('pml-audio-state',{detail:state}));
    }

    unlock(gesture = false) {
      if (!this.enabled || this.unavailable) return;
      if (!this.context) {
        try {
          this.context = new (window.AudioContext || window.webkitAudioContext)();
          this.created++;
          this.master = this.context.createGain();
          this.compressor = this.context.createDynamicsCompressor();
          this.compressor.threshold.value = -12;
          this.compressor.ratio.value = 8;
          this.master.connect(this.compressor).connect(this.context.destination);
          for (const group of Object.keys(defaults).filter(group => group !== 'master')) {
            this.groups[group] = this.context.createGain();
            this.groups[group].connect(this.master);
          }
          const samples = this.context.sampleRate;
          this.noise = this.context.createBuffer(1,samples,samples);
          const waveform = this.noise.getChannelData(0);
          // Local PRNG keeps the game's seeded random sequence independent of sound.
          let seed = 8233;
          for (let i=0;i<samples;i++) {
            seed = (Math.imul(seed,1664525)+1013904223) >>> 0;
            waveform[i] = seed/2147483648-1;
          }
          this.context.addEventListener('statechange', () => {
            if (this.context.state === 'running') this.started = true;
            this.report();
            if (this.started && this.context.state === 'suspended' && !document.hidden) this.unlock();
          });
          this.mix();
        } catch {
          this.unavailable = true;
          this.report();
          return;
        }
      }
      this.report();
      if (this.context.state === 'running') {
        this.started = true;
        if (gesture) this.play('ready');
      } else if (gesture || this.started) {
        if (this.resumePending && !gesture) return;
        this.resumePending = true;
        this.context.resume().then(() => {
          this.started = this.context.state === 'running';
          this.report();
          if (gesture) this.play('ready');
        }).catch(error => console.warn('Pixel Meow audio:',error.message))
          .finally(() => { this.resumePending = false; });
      }
    }

    mix() {
      if (!this.master) return;
      const at = this.context.currentTime;
      this.master.gain.setTargetAtTime(this.enabled ? this.volume.master : 0,at,.025);
      for (const [group,node] of Object.entries(this.groups)) {
        const quiet = this.ducked && ['combat','spawn','ambient'].includes(group);
        node.gain.setTargetAtTime(this.volume[group]*(quiet ? .18 : 1),at,.035);
      }
    }

    duck(on) {
      this.ducked = on;
      this.mix();
    }

    play(name) {
      if (!this.enabled || this.context?.state !== 'running') return false;
      const [group,cooldown,priority,notes] = profiles[name];
      if (!this.volume.master || !this.volume[group]) return false;
      const now = this.context.currentTime;
      if (now - (this.last.get(name) ?? -Infinity) < cooldown ||
          (group === 'combat' && now - (this.last.get(group) ?? -Infinity) < .08)) return false;
      const overflow = this.voices.size + notes.length - maxVoices;
      if (overflow > 0) {
        const victims = [...this.voices].filter(voice => voice.priority < priority)
          .sort((a,b) => a.priority-b.priority);
        if (victims.length < overflow) return false;
        for (const voice of victims.slice(0,overflow)) {
          voice.source.stop();
          this.release(voice);
        }
      }
      this.last.set(name,now);
      this.last.set(group,now);
      for (const [frequency,length,type,level,slide,delay=0] of notes) {
        const start = now+delay, stop = start+length;
        const gain = this.context.createGain();
        gain.gain.setValueAtTime(.0001,start);
        gain.gain.linearRampToValueAtTime(level,start+.008);
        gain.gain.exponentialRampToValueAtTime(.0001,stop);
        const source = frequency ? this.context.createOscillator() : this.context.createBufferSource();
        if (frequency) {
          source.type = type;
          source.frequency.setValueAtTime(frequency,start);
          if (slide) source.frequency.exponentialRampToValueAtTime(slide,stop);
        } else source.buffer = this.noise;
        source.connect(gain).connect(this.groups[group]);
        const voice = {source,gain,priority};
        this.voices.add(voice);
        source.onended = () => this.release(voice);
        source.start(start);
        source.stop(stop);
      }
      this.peakVoices = Math.max(this.peakVoices,this.voices.size);
      if (test) this.events[name] = (this.events[name] || 0)+1;
      return true;
    }

    release(voice) {
      if (!this.voices.delete(voice)) return;
      voice.source.onended = null;
      voice.source.disconnect();
      voice.gain.disconnect();
    }
  }

  window.pmlAudio = new AudioManager();
})();
