'use strict';

(() => {
  const maps = I18N.en.st;
  const teams = {
    cat: {side: 0, name: 'Mèo', defs: Object.fromEntries(PU.map(def => [def.id, def]))},
    enemy: {side: 1, name: 'Quái', defs: EU},
  };
  const names = {
    basic:'Basic Cat', tank:'Tank Cat', archer:'Archer Cat', ninja:'Ninja Cat', mecha:'Mecha Cat',
    slime:'Slime', hog:'Hog', turtle:'Turtle', crow:'Crow', boar:'Boar Boss',
  };
  const skills = {fire_cat_cannon:'Pháo Mèo', fire_enemy_cannon:'Pháo Quái', change_map:'Đổi bản đồ',
    heal_base:'Hồi căn cứ', buff_team:'Tăng sức mạnh'};
  const defaults = {audio:true, vfx:true, auto_cannon:true};
  const finishActions = {cat:'finish_battle_cat', enemy:'finish_battle_enemy'};
  const maxUnits = 24;
  const aiUnits = 13;
  const maxBatch = 16;
  const resultSeconds = 5;
  const maxMapRequests = 3;
  const aiPlans = [
    ['basic','tank','archer','basic','ninja','basic','archer','tank','basic','mecha'],
    ['slime','turtle','crow','hog','slime','hog','crow','turtle','slime','boar'],
  ];
  // Asymmetric original stats need separate AI tuning. Gift units use the unscaled original definitions.
  const enemyAIScale = 1.31;
  const aiScales = [
    {basic:1.03,tank:1,archer:1,ninja:.9,mecha:.85},
    {slime:1.3,turtle:.87,crow:1.15,hog:1.21,boar:.56},
  ];
  const make = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const write = (id, value) => {
    if ($(id).textContent !== String(value)) $(id).textContent = value;
  };
  function sprite(canvas, frame) {
    canvas.width = canvas.height = 48;
    const scale = Math.min(2, 44 / Math.max(frame.width,frame.height));
    const w = Math.round(frame.width * scale), h = Math.round(frame.height * scale);
    const x = canvas.getContext('2d');
    x.imageSmoothingEnabled = false;
    x.drawImage(frame, (48-w)>>1, (48-h)>>1, w, h);
    return canvas;
  }
  function giftLine(row) {
    const line = make('div', '', 'live-gift');
    if (!row) {
      line.append(make('span', 'Tự ra trận'));
      return line;
    }
    if (row.icon) {
      const icon = make('span', '', 'gift-icon');
      const img = make('img');
      img.alt = '';
      img.referrerPolicy = 'no-referrer';
      img.addEventListener('error', () => icon.remove(), {once:true});
      icon.replaceChildren(img);
      img.src = row.icon;
      line.append(icon);
    }
    const name = make('span', row.live_label || row.display_label || row.name || ('ID ' + row.id));
    name.title = row.name || name.textContent;
    line.append(name);
    if (row.coins != null) line.append(make('b', row.coins.toLocaleString('vi-VN') + ' xu','gift-price'));
    if (row.action === 'spawn') line.append(make('b', '×' + row.quantity));
    if (row.action === 'heal_base') line.append(make('b', '+' + row.quantity + '%'));
    if (row.action === 'buff_team') line.append(make('b', '+25%'));
    return line;
  }

  class AutoBattleController {
    constructor() {
      this.config = defaults;
      this.map = 0;
      this.mapQueue = [];
      this.spawnQueue = [];
      this.receipts = new Map();
      this.lastResult = null;
      this.notices = [];
      this.noticeUntil = 0;
      this.teamNoticeUntil = 0;
      this.cheers = {cat:0, enemy:0};
      this.cheersUntil = 0;
      this.actionClock = 0;
      this.stats = this.loadStats();
      this.guidePage = 0;
      this.guideTime = 0;
      this.telemetryTime = 0;
      this.frames = 0;
      this.retries = 0;
      this.player = crypto.randomUUID();
      this.canSaveStats = false;
      this.catalog = {status:'missing', rows:[]};
    }
    start() {
      $('live-field').append(cv, $('flash'));
      $('live').hidden = false;
      document.documentElement.lang = 'vi';
      document.title = 'Pixel Meow Legion · TikTok LIVE';
      this.renderGuide();
      window.addEventListener('pml-audio-state', () => this.send({type:'state', state:this.snapshot()}));
      this.startRound();
      pmlAudio.configure(this.config.audio);
      this.connect();
    }
    loadStats() {
      const stats = {cat:0, enemy:0, draw:0};
      try {
        const saved = JSON.parse(localStorage.getItem('pml_live_stats_v1'));
        for (const key of Object.keys(stats)) {
          if (Number.isSafeInteger(saved?.[key]) && saved[key]>=0) stats[key] = saved[key];
        }
      } catch (error) { console.warn('LIVE stats:', error); }
      return stats;
    }
    startRound(mapChanged = false) {
      this.roundId = crypto.randomUUID();
      this.finisher = null;
      pmlAudio.duck(false);
      this.autoCannonAt = 24;
      $('live-finisher').hidden = true;
      newGame(this.map);
      this.roundNumber = this.stats.cat + this.stats.enemy + this.stats.draw;
      this.actionClock = 0;
      this.siege = [0,0];
      this.ai = [{remaining:.2,index:0}, {remaining:.2,index:0}];
      this.buff = {cat:0, enemy:0};
      this.cooldowns = new Map();
      this.enemyBeam = null;
      write('live-map', maps[this.map]);
      $('live-result').hidden = true;
      this.refreshNext();
      pmlAudio.play(mapChanged ? 'map' : 'battleStart');
      this.send({type:'state', state:this.snapshot()});
    }
    connect() {
      const url = new URL('/ws', location.href);
      url.searchParams.set('player', this.player);
      url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const socket = new WebSocket(url);
      this.socket = socket;
      socket.addEventListener('message', event => {
        const data = JSON.parse(event.data);
        if (data.type === 'owner') {
          if (!this.canSaveStats) this.stats = this.loadStats();
          this.canSaveStats = true;
          this.retries = 0;
          this.standby = false;
          if (this.lastResult) this.send(this.lastResult);
          this.send({type:'state', state:this.snapshot()});
        }
        if (data.type === 'standby') {
          this.canSaveStats = false;
          write('live-catalog', 'Nguồn phụ · đang chờ');
          this.standby = true;
        }
        if (data.type === 'config') {
          this.config = {...defaults, ...data.settings};
          pmlAudio.configure(this.config.audio,this.config.audio_mix);
          vfxOn = this.config.vfx;
          if (vfxOn) startWeather();
          else G.fx.length = G.fxq.length = G.wx.length = 0;
          this.connection = data.connection;
          this.catalog = data.catalog;
          this.guideTime = 0;
          this.guidePage = 0;
          this.renderGuide();
        }
        if (data.type === 'action' && !this.standby) this.receive(data.action);
        if (data.type === 'cheers' && !this.standby) {
          this.cheers = data.rates;
          this.cheersUntil = performance.now() + 1800;
        }
        if (data.type === 'team_selected' && !this.standby && Object.hasOwn(teams,data.team)) {
          const notice = $('live-team-notice');
          notice.className = data.team === 'cat' ? 'cats' : 'enemies';
          write('live-team-viewer', data.sender);
          write('live-team-selection', 'chọn phe ' + teams[data.team].name);
          notice.hidden = false;
          this.teamNoticeUntil = performance.now() + 3200;
        }
        if (data.type === 'notice') this.notice(data.text);
      });
      socket.addEventListener('close', () => {
        this.cheersUntil = 0;
        this.connection = 'backend-offline';
        write('live-catalog', 'Mất kết nối máy chủ');
        setTimeout(() => this.connect(), Math.min(8000, 500 * 2 ** this.retries++));
      });
    }
    send(message) {
      if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(message));
    }
    ack(action, status, detail, final = true) {
      const message = {type:'ack', id:action.id, status, detail, final, round_id:this.roundId};
      this.receipts.set(action.id, message);
      if (this.receipts.size > 512) this.receipts.delete(this.receipts.keys().next().value);
      this.send(message);
    }
    notice(text) {
      if (this.notices.length >= 12) this.notices.shift();
      this.notices.push(text);
    }
    refreshNext() {
      const request = this.mapQueue[0];
      write('live-next', request ? request.sender + ' → ' + maps[request.map] +
        (this.mapQueue.length > 1 ? ' · +' + (this.mapQueue.length - 1) + ' lượt chờ' : '') : '');
    }
    receive(action) {
      const previous = this.receipts.get(action.id);
      if (previous) {
        this.send(previous);
        return;
      }
      const {kind, team, unit, sender} = action;
      const reject = reason => {
        this.notice(sender + ' · ' + reason);
        this.ack(action, 'rejected', reason);
      };
      if (Date.now() >= action.expires_at) return reject('Lượt quà đã hết hạn');
      const key = action.mapping_id || [kind,team,unit].join('/');
      if (kind === 'change_map') {
        if ((this.cooldowns.get(key) ?? 0) > G.time) return reject('Quà đổi bản đồ đang hồi');
        if (this.mapQueue.length >= maxMapRequests) return reject('Hàng đợi đổi bản đồ đã đầy');
        const lastMap = this.mapQueue.at(-1)?.map ?? this.map;
        action.map = (lastMap + 1) % maps.length;
        this.mapQueue.push(action);
        this.cooldowns.set(key, G.time + action.cooldown);
        this.refreshNext();
        this.notice(sender + ' · Đã xếp lịch ' + maps[action.map]);
        this.ack(action, 'queued', maps[action.map], false);
        return;
      }
      if (this.finisher) return reject('Kết quả đã khóa bởi quà kết liễu');
      if (kind === 'restart') {
        this.cancelSpawns('Khởi động lại mô phỏng');
        this.startRound();
        this.ack(action, 'executed', maps[this.map]);
        return;
      }
      if (G.over) return reject('Trận đã kết thúc');
      if ((this.cooldowns.get(key) ?? 0) > G.time) return reject('Quà đang hồi');
      if (Object.hasOwn(finishActions,team) && kind === finishActions[team]) {
        if (action.round_id !== this.roundId) return reject('Quà kết liễu thuộc trận trước');
        this.startFinisher(action);
        return;
      }
      if (kind === 'spawn' && Object.hasOwn(teams[team]?.defs ?? {}, unit)) {
        if (this.spawnQueue.length >= 24) return reject('Hàng đợi triệu hồi đã đầy');
        action.remaining = Math.min(maxBatch, action.quantity);
        action.spawned = 0;
        action.until = G.time + 12;
        this.spawnQueue.push(action);
        this.cooldowns.set(key, G.time + action.cooldown);
        this.ack(action, 'queued', 'Đang chờ triệu hồi', false);
        return;
      }
      const cannon = kind === 'fire_cat_cannon' || kind === 'fire_enemy_cannon';
      if (cannon) {
        if (kind === 'fire_cat_cannon') {
          if (G.cannon < CANNON_CHARGE) return reject('Pháo Mèo đang hồi');
          fireCannon();
        } else if (!this.fireEnemyCannon()) return reject('Pháo Quái đang hồi');
      } else if (kind === 'heal_base' && teams[team]) {
        const base = team === 'cat' ? G.pb : G.eb;
        if (G.time < (base.healUntil ?? 0)) return reject('Hồi căn cứ đang hồi');
        if (base.hp >= base.maxHp) return reject('Căn cứ đang đầy HP');
        base.hp = Math.min(base.maxHp, base.hp + Math.round(base.maxHp * action.quantity / 100));
        base.healUntil = G.time + 15;
        spawnFx(G.fx,'heartPop',base.x,GROUND-25,{count:6},true);
      } else if (kind === 'buff_team' && teams[team]) {
        if (this.buff[team] > G.time) return reject('Tăng sức mạnh đang hoạt động');
        this.buff[team] = G.time + action.quantity;
        spawnFx(G.fx,'firework',team === 'cat' ? PB_X : EB_X,GROUND-58,{light:.8});
      } else return reject('Hành động không hợp lệ');
      if (!cannon) pmlAudio.play('power');
      this.cooldowns.set(key, G.time + action.cooldown);
      const detail = skills[kind] + (cannon ? '' : ' · ' + teams[team].name);
      this.notice(sender + ' · ' + detail + (action.quantity > 1 && cannon ? ' · 1 phát mỗi lượt quà' : ''));
      this.ack(action, 'executed', detail + (action.quantity > 1 && cannon ? ' (1 phát mỗi lượt quà)' : ''));
    }
    startFinisher(action) {
      const cat = action.team === 'cat';
      this.finisher = {action, elapsed:0, from:cat ? PB_X + 20 : EB_X - 20,
        to:cat ? EB_X : PB_X, hit:false};
      G.beam = this.enemyBeam = null;
      this.cancelSpawns('Kết quả đã khóa bởi quà kết liễu');
      const title = $('live-finisher');
      title.className = cat ? 'cats' : 'enemies';
      write(title.id, '👑 ' + action.sender.slice(0,32) + ' · KẾT LIỄU PHE ' + (cat ? 'QUÁI' : 'MÈO'));
      title.hidden = false;
      spawnFx(G.fx,'spawnPortal',this.finisher.from,GROUND-20,{width:35,light:1},true);
      pmlAudio.duck(true);
      pmlAudio.play('finisherCue');
      this.ack(action, 'queued', 'Đã khóa kết quả cho phe ' + teams[action.team].name, false);
    }
    advanceFinisher(dt) {
      const strike = this.finisher;
      strike.elapsed += dt;
      if (!strike.launched && strike.elapsed >= .85) {
        strike.launched = true;
        pmlAudio.play('finisherLaunch');
      }
      const target = strike.action.team === 'cat' ? G.eb : G.pb;
      if (!strike.hit && strike.elapsed >= 2.3) {
        strike.hit = true;
        G.shake = .8;
        pmlAudio.play('finisherImpact');
        for (let i=0;i<4;i++) {
          queueFx(i*.24,'smallExplosion',strike.to + (i%2 ? -8 : 8),GROUND-22-i*4,
            {size:1,light:1,stone:G.st.dirt});
        }
        hitParts(strike.to,GROUND-35,'#ffe5a1',14);
      }
      if (strike.elapsed < 3.6) return;
      this.finisher = null;
      damage(target,target.hp,false);
      this.endRound(strike.action);
      this.ack(strike.action,'executed','Phe ' + teams[strike.action.team].name + ' chiến thắng');
    }
    drawFinisher() {
      if (!this.finisher) return;
      const {action,elapsed,from,to,hit} = this.finisher;
      const color = action.team === 'cat' ? '#80eaa5' : '#ff79be';
      const y = GROUND-42;
      ctx.save();
      ctx.fillStyle = color;
      if (hit) {
        ctx.globalAlpha = Math.max(0,1-(elapsed-2.3)/.65);
        drawRing(to,y,4+(elapsed-2.3)*45,color);
      } else {
        const progress = Math.max(0,(elapsed-.85)/1.45);
        const direction = Math.sign(to-from);
        const x = Math.round(from + (to-from)*progress);
        const pulse = Math.min(9,Math.floor(3+elapsed*10));
        ctx.fillRect(x-pulse,y-pulse,2*pulse+1,2*pulse+1);
        ctx.fillStyle = '#fff1c9';
        ctx.fillRect(x-3,y-3,7,7);
        for (let i=1;i<=7 && progress>0;i++) {
          ctx.globalAlpha = 1-i/8;
          ctx.fillStyle = color;
          ctx.fillRect(x-direction*i*6,y-i%3,6,4);
        }
      }
      ctx.restore();
    }
    fireEnemyCannon() {
      if (G.over || G.enemyCannon > 0) return false;
      G.enemyCannon = CANNON_CHARGE;
      this.enemyBeam = {t:0, hit:false};
      G.shake = .5;
      flash(.35);
      pmlAudio.play('cannonEnemy');
      return true;
    }
    drawEnemyBeam() {
      if (!this.enemyBeam) return;
      const {t} = this.enemyBeam;
      const length = Math.min(1,t/.3) * CANNON_RANGE;
      const x = EB_X - 28 - length, y = GROUND - 31;
      ctx.globalAlpha = t < .6 ? 1 : Math.max(0, 1-(t-.6)/.4);
      for (const [color,h] of [['#743ed8',9],['#e880ff',5],['#fef0ff',1]]) {
        ctx.fillStyle = color;
        ctx.fillRect(Math.round(x),y-(h>>1),Math.round(length),h);
      }
      ctx.globalAlpha = 1;
    }
    spawn(team, unit, gift) {
      const {side,defs} = teams[team];
      const own = G.units.filter(u => u.side === side);
      const heavy = unit === 'boar' || unit === 'mecha';
      if (own.length >= (gift ? maxUnits : aiUnits)) return false;
      if (heavy && own.some(u => u.def.id === unit)) return false;
      // Leave the heavy-unit slot open to gifts once the current AI unit dies.
      if (heavy && !gift && this.spawnQueue.some(a => a.team === team && a.unit === unit)) return false;
      // Alternate short offensives on a fixed schedule so a quiet match still progresses.
      const attacking = Math.floor(G.time / 34 + this.roundNumber) % 2 === side;
      const surge = attacking && G.time % 34 >= 16 ? 1.3 : 1;
      const u = spawnUnit(side,defs[unit],gift ? 1 : aiScales[side][unit] * surge * (side ? enemyAIScale : 1));
      if (gift || heavy) {
        spawnFx(G.fx,'spawnPortal',u.x,u.y,{width:heavy?32:19,skip:.2,light:.65},true);
      }
      return true;
    }
    spawnRate(team) {
      const likesPerSecond = performance.now() < this.cheersUntil ? this.cheers[team] : 0;
      return 1 + Math.min(1, likesPerSecond / 20);
    }
    runAI(team,dt) {
      const {side} = teams[team];
      const ai = this.ai[side], plan = aiPlans[side];
      ai.remaining -= dt * this.spawnRate(team);
      if (ai.remaining > 0) return;
      // Increase tempo between rounds, without reacting to score, gifts or opponent strength.
      const interval = Math.max(2.65, 4.4 - this.roundNumber * .07);
      ai.remaining += interval + (side ? .1 : 0);
      const unit = plan[ai.index++ % plan.length];
      if (!this.spawn(team,unit,false) && (unit === 'boar' || unit === 'mecha')) {
        this.spawn(team,plan[0],false);
      }
    }
    finishSpawn(action, reason = '') {
      const count = action.spawned;
      const unit = names[action.unit];
      const detail = count + '/' + action.quantity + ' ' + unit + (reason ? ' · ' + reason : '');
      this.notice(action.sender + ' · ' + detail);
      this.ack(action, count === action.quantity ? 'executed' : count ? 'partial' : 'rejected', detail);
    }
    cancelSpawns(reason) {
      for (const action of this.spawnQueue.splice(0)) this.finishSpawn(action,reason);
    }
    step(dt) {
      if (G.over) {
        update(dt);
        if (G.overT >= resultSeconds) {
          const request = this.mapQueue.shift();
          this.map = request?.map ?? (this.map + 1) % maps.length;
          this.startRound(Boolean(request));
          if (request) {
            this.notice(request.sender + ' · Đã chuyển đến ' + maps[this.map]);
            this.ack(request, 'executed', maps[this.map]);
          }
        }
        return;
      }
      if (this.finisher) {
        update(dt);
        return;
      }
      this.runAI('cat',dt);
      this.runAI('enemy',dt);
      if (G.time + 1e-6 >= this.actionClock) {
        this.actionClock = G.time + .12;
        for (const [i,action] of this.spawnQueue.entries()) {
          if (G.time >= action.until || Date.now() >= action.expires_at) {
            this.spawnQueue.splice(i,1);
            this.finishSpawn(action,'Hết thời gian chờ chỗ trống');
            break;
          }
          if (!this.spawn(action.team,action.unit,true)) continue;
          if (!action.spawned && action.gift_id != null && ['mecha','boar'].includes(action.unit)) pmlAudio.play('gift');
          action.spawned++;
          action.remaining--;
          if (!action.remaining) {
            this.spawnQueue.splice(i,1);
            this.finishSpawn(action, action.quantity > maxBatch ? 'Giới hạn 16 quân mỗi lượt quà' : '');
          }
          break;
        }
      }
      for (const u of G.units) {
        const team = u.side === 0 ? 'cat' : 'enemy';
        u.liveAtkM ??= u.atkM;
        u.atkM = u.liveAtkM * (this.buff[team] > G.time ? 1.25 : 1);
      }
      update(dt);
      if (this.config.auto_cannon && !G.over && G.time >= this.autoCannonAt) {
        // The cue requires a target in range. Both sides keep the existing shared manual/auto cooldown.
        if (G.cannon >= CANNON_CHARGE && G.units.some(u=>u.side===1 && u.hp>0 && u.x<=PB_FRONT+CANNON_RANGE)) fireCannon();
        if (G.units.some(u=>u.side===0 && u.hp>0 && u.x>=EB_FRONT-CANNON_RANGE)) this.fireEnemyCannon();
        this.autoCannonAt = G.time + 5;
      }
    }
    advanceSiege(dt) {
      // A forward ranged unit can shell the opposing base while its team holds the lane.
      const middle = (PB_FRONT + EB_FRONT) / 2;
      for (const {side} of Object.values(teams)) {
        const dir = side ? -1 : 1;
        const target = side ? G.pb : G.eb;
        const archer = G.units.find(u => u.side === side && u.hp > 0 && u.state !== 'kb' &&
          u.def.proj && (u.x-middle)*dir > 4);
        if (!archer) {
          this.siege[side] = 0;
          continue;
        }
        this.siege[side] += dt;
        if (this.siege[side] < 3 || G.projs.length >= 96) continue;
        this.siege[side] = 0;
        G.projs.push({k:archer.def.proj,x:archer.x,y:archer.y-(archer.def.fly?24:9),
          tgt:target,tx:target.x,dmg:60,side,dir});
      }
    }
    afterCombat(dt) {
      if (this.finisher) {
        this.advanceFinisher(dt);
        return;
      }
      G.enemyCannon = Math.max(0,G.enemyCannon - dt);
      if (this.enemyBeam) {
        const beam = this.enemyBeam;
        beam.t += dt;
        if (!beam.hit && beam.t >= .35) {
          beam.hit = true;
          for (const unit of G.units) {
            if (unit.side === 0 && !unit.dead && unit.x >= EB_FRONT-CANNON_RANGE) {
              damage(unit,160,true);
            }
          }
          for (let i=0,x=EB_FRONT-26;x>=EB_FRONT-CANNON_RANGE;x-=34,i++) {
            queueFx(i*.06,'smallExplosion',x,LANE.bot-4,{size:.42,light:.8,stone:G.st.dirt});
          }
        }
        if (beam.t >= 1) this.enemyBeam = null;
      }
      const {pb,eb} = G;
      if (pb.hp > 0 && eb.hp > 0) {
        this.advanceSiege(dt);
        return;
      }
      this.endRound();
    }
    endRound(finisher = null) {
      const {pb,eb} = G;
      const winner = pb.hp > 0 ? 'cat' : eb.hp > 0 ? 'enemy' : 'draw';
      const reason = finisher ? 'Quà kết liễu' : winner === 'draw' ? 'Hai căn cứ bị phá cùng lúc' : 'Căn cứ đối phương bị phá';
      G.over = {cat:'win',enemy:'lose',draw:'draw'}[winner];
      G.beam = this.enemyBeam = null;
      G.overT = 0;
      this.stats[winner]++;
      if (this.canSaveStats) {
        try { localStorage.setItem('pml_live_stats_v1', JSON.stringify(this.stats)); }
        catch (error) { console.warn('LIVE stats:', error); }
      }
      this.cancelSpawns('Trận đã kết thúc');
      write('live-result', {cat:'PHE MÈO CHIẾN THẮNG',enemy:'PHE QUÁI CHIẾN THẮNG',draw:'HAI PHE HÒA'}[winner]);
      $('live-result').hidden = false;
      for (const base of [pb,eb]) if (base.hp <= 0) {
        queueFx(0,'smallExplosion',base.side ? EB_X : PB_X,GROUND+2-BASE_LIFT,{size:.85,light:.9});
      }
      pmlAudio.play('collapse');
      pmlAudio.play(winner === 'cat' ? 'winCat' : winner === 'enemy' ? 'winEnemy' : 'draw');
      if (winner !== 'draw') {
        queueFx(.2,'confetti',160,GROUND+2,{});
        for (let i=0;i<3;i++) {
          queueFx(i*.22,'firework',(winner === 'cat' ? 55 : 185)+i*34,GROUND-58,{light:.8});
        }
      }
      this.lastResult = {type:'result', winner, reason, finisher:finisher?.sender, state:this.snapshot()};
      this.send(this.lastResult);
    }
    snapshot() {
      return {round_id:this.roundId, audio:pmlAudio.state, map:maps[this.map], time:Math.floor(G.time), cat_hp:G.pb.hp, enemy_hp:G.eb.hp,
        cat_units:G.units.filter(u => u.side === 0).length, enemy_units:G.units.filter(u => u.side === 1).length,
        wins:{...this.stats}, pending_maps:this.mapQueue.length, pending_spawns:this.spawnQueue.length};
    }
    paint(dt) {
      for (const [team,base] of [['cat',G.pb],['enemy',G.eb]]) {
        write('live-'+team+'-hp', Math.ceil(base.hp) + ' / ' + base.maxHp);
        $(`live-${team}-bar`).style.transform = 'scaleX(' + base.hp/base.maxHp + ')';
      }
      write('live-wins', this.stats.cat + ' · ' + this.stats.enemy);
      if (this.teamNoticeUntil && performance.now() >= this.teamNoticeUntil) {
        $('live-team-notice').hidden = true;
        this.teamNoticeUntil = 0;
      }
      for (const team of Object.keys(teams)) {
        const percent = Math.round((this.spawnRate(team)-1)*100);
        write('live-'+team+'-boost', percent ? '♥+'+percent+'%' : '');
      }
      if (performance.now() >= this.noticeUntil) {
        this.currentNotice = this.notices.shift();
        this.noticeUntil = performance.now() + (this.currentNotice ? 2750 : 350);
      }
      write('live-activity', this.currentNotice || '');
      this.guideTime += dt;
      if (this.guideTime >= 12 && this.catalog.rows.length) {
        this.guideTime = 0;
        this.guidePage++;
        this.renderGuide();
      }
      this.telemetryTime += dt;
      this.frames++;
      if (this.telemetryTime >= 5) {
        this.send({type:'state', state:{...this.snapshot(),fps:Math.round(this.frames/this.telemetryTime)}});
        this.telemetryTime = this.frames = 0;
      }
    }
    renderGuide() {
      const rows = this.catalog.rows;
      const badges = {mock:'Quà mô phỏng',verified:'Quà của phiên LIVE',cached:'Danh mục lưu · chưa xác minh',
        observed:'Quà nhận trong phiên',missing:'Đang chờ danh mục quà'};
      const connectionLabels = {unconfigured:'Chưa cấu hình TikTok', connecting:'Đang kết nối TikTok',
        'backend-offline':'Mất kết nối máy chủ', connected:'TikTok đã kết nối', disconnected:'TikTok mất kết nối', offline:'Đang chờ phiên LIVE', mock:badges.mock};
      const status = this.standby ? 'Nguồn phụ · đang chờ' : connectionLabels[this.connection];
      const catalogLabel = badges[this.catalog.status] || badges.missing;
      const showCatalog = !status || (!this.standby && this.connection === 'connected' && this.catalog.status !== 'mock');
      write('live-catalog', showCatalog ? catalogLabel : status);
      $('live-catalog').title = catalogLabel;
      const display = matches => {
        if (matches.length < 2 || matches[0].tier === 'common') return matches[0];
        return matches[this.guidePage % matches.length];
      };
      for (const [team,{defs}] of Object.entries(teams)) {
        const items = Object.values(defs).map(def => {
          const item = make('li');
          const entry = make('div','','live-entry');
          const row = display(rows.filter(r => r.action === 'spawn' && r.team === team && r.unit === def.id));
          entry.append(make('strong',names[def.id]), giftLine(row));
          item.append(sprite(make('canvas'),def.art.walk[0]),entry);
          return item;
        });
        $('live-'+team+'-list').replaceChildren(...items);
      }
      const actions = Object.keys(skills).slice(0,3).map(action => ({action}));
      for (const action of ['heal_base','buff_team']) {
        for (const team of Object.keys(teams)) {
          if (rows.some(row => row.action === action && row.team === team)) actions.push({action,team});
        }
      }
      const pageCount = Math.ceil(actions.length/3);
      const page = this.guidePage % pageCount;
      const items = actions.slice(page*3,page*3+3).map(({action,team}) => {
        const row = display(rows.filter(r => r.action === action && (!team || r.team === team)));
        const item = make('li');
        const entry = make('div','','live-entry');
        const title = skills[action] + (team ? ' · ' + teams[team].name : '');
        const line = giftLine(row);
        if (!row) line.replaceChildren(make('span','Chờ ghép quà'));
        entry.append(make('strong',title),line);
        item.append(entry);
        return item;
      });
      $('live-skill-list').replaceChildren(...items);
      $('live-finish-list').replaceChildren(...Object.entries(finishActions).map(([team,action]) => {
        const row = rows.find(r => r.action === action && r.verified);
        const item = make('li','',team === 'cat' ? 'cats' : 'enemies');
        const line = giftLine(row && {...row,display_label:''});
        if (!row) line.replaceChildren(make('span','Chưa xác minh quà'));
        item.append(make('strong',team === 'cat' ? 'MÈO THẮNG' : 'QUÁI THẮNG'),line);
        return item;
      }));
    }
  }
  window.pmlLive = new AutoBattleController();
})();
