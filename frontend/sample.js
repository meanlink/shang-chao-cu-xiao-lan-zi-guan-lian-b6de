// 门店补传更正 / 撤销：整票版本化，晚到的旧版本会被后端乐观锁拒绝。

const opLabels = { create: '新票入篮', correct: '整票更正', void: '整票撤销' };

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[ch]);
}

function ticketForm(ctx, kind) {
  const isVoid = kind === 'void';
  return `
    <form id="${kind}-form" class="correction-form">
      <label class="field"><span>更正票批次号（幂等键）</span>
        <input name="batch_no" required pattern="[A-Za-z0-9-]{3,40}"
          value="${kind.toUpperCase()}-${new Date().toISOString().slice(0, 10).replace(/-/g, '')}-01"></label>
      <label class="field"><span>来源文件</span>
        <input name="source_file" value="${isVoid ? 'orders_revocation.csv' : 'orders_correction.csv'}"></label>
      <div class="form-row">
        <label class="field"><span>小票外部号</span><input name="external_id" required placeholder="POS-1120-0003"></label>
        <label class="field"><span>客户端依据版本（乐观锁，留空不校验）</span>
          <input name="expected_version" type="number" min="1" placeholder="如 1"></label>
      </div>
      ${isVoid ? `
        <label class="field"><span>撤销原因</span><input name="reason" required placeholder="收银已撤销该小票"></label>
      ` : `
        <div class="form-row">
          <label class="field"><span>门店编码</span><input name="store_code" required placeholder="BJ-CY"></label>
          <label class="field"><span>营业日期</span><input name="order_date" type="date"></label>
        </div>
        <label class="field"><span>整票明细（每行：SKU,件数[,单价]）</span>
          <textarea name="lines" required placeholder="CF-001,2&#10;ML-001,1&#10;BK-002,3,22.00"></textarea></label>
      `}
      <label class="field"><span>备注</span><input name="note" maxlength="240"></label>
      <p class="promo-error" role="alert" hidden></p>
      <button class="btn orange" type="submit">${isVoid ? '提交撤销票' : '提交整票更正'}</button>
    </form>`;
}

function parseLines(raw) {
  const lines = [];
  for (const chunk of raw.split(/\r?\n/).map(s => s.trim()).filter(Boolean)) {
    const parts = chunk.split(',').map(s => s.trim());
    if (parts.length < 2) throw new Error(`明细格式错误：${chunk}，应为 SKU,件数[,单价]`);
    const [sku, qty, price] = parts;
    const quantity = Number(qty);
    if (!Number.isInteger(quantity) || quantity < 1) throw new Error(`SKU ${sku} 件数必须是正整数`);
    const line = { sku, quantity };
    if (price) {
      const unit_price = Number(price);
      if (!(unit_price > 0)) throw new Error(`SKU ${sku} 单价不合法`);
      line.unit_price = unit_price;
    }
    lines.push(line);
  }
  return lines;
}

function readForm(form, kind) {
  const fd = new FormData(form);
  const base = {
    batch_no: fd.get('batch_no'),
    source_file: fd.get('source_file') || undefined,
    note: fd.get('note') || '',
    external_id: fd.get('external_id'),
    expected_version: fd.get('expected_version') ? Number(fd.get('expected_version')) : null,
  };
  if (kind === 'void') {
    return { ...base, reason: fd.get('reason') };
  }
  return {
    ...base,
    store_code: fd.get('store_code'),
    order_date: fd.get('order_date') || null,
    lines: parseLines(fd.get('lines')),
  };
}

async function showHistory(ctx, externalId) {
  const detail = await ctx.api(`/orders/tickets/${encodeURIComponent(externalId)}`);
  const order = detail.order;
  const rows = detail.revisions.map(r => `
    <tr>
      <td>v${r.revision_seq}</td>
      <td><span class="status status-${r.op === 'void' ? 'rejected' : r.op === 'correct' ? 'submitted' : 'approved'}">${opLabels[r.op] || r.op}</span></td>
      <td class="mono">${escapeHtml(r.batch_no)}</td>
      <td>${escapeHtml(r.created_by_name)}</td>
      <td style="max-width:280px">${escapeHtml(r.snapshot_json || '')}</td>
      <td>${String(r.created_at).slice(0, 19)}</td>
    </tr>`).join('');
  ctx.shell(`
    <div class="topline"><div><div class="eyebrow">TICKET LEDGER</div>
      <h2>小票 ${escapeHtml(order.external_id)} 的版本留痕</h2>
      <div style="color:var(--muted);font-size:13px;margin-top:10px">
        当前版本 v${order.version} · 状态 ${order.status === 'voided' ? '已撤销（不参与重算）' : '完整入篮（参与重算）'}
      </div></div></div>
    <div class="panel" style="margin-top:16px">
      <div class="table-wrap"><table class="evidence-table"><thead><tr>
        <th>版本</th><th>操作</th><th>更正票批次</th><th>操作人</th><th>整票快照</th><th>时间</th>
      </tr></thead><tbody>${rows}</tbody></table></div>
      <div style="padding:14px"><button class="btn alt small" data-back="orders">返回订单导入</button></div>
    </div>`);
  document.querySelector('[data-back]').onclick = () => ctx.refresh();
}

export async function sampleDesk(ctx) {
  const [orders, batches, version] = await Promise.all([
    ctx.api('/orders'), ctx.api('/orders/batches'), ctx.api('/orders/sample-version'),
  ]);
  const content = ctx.head(
    'INGEST / POS CORRECTIONS', '订单补传与撤销',
    '整票更正与撤销按版本留痕；晚到的旧版本不能覆盖新票，撤销票不会再被加回样本。') + `
    <div class="grid">
      <div class="panel span-4">
        <h3>样本版本</h3>
        <div class="metric"><strong>v${version.current_version}</strong>
          <span class="kicker">每次接受补传/撤销递增 · 更新于 ${String(version.updated_at).slice(0, 19)}</span></div>
        <h3 style="margin-top:18px">整票更正</h3>
        ${ticketForm(ctx, 'correct')}
      </div>
      <div class="panel span-4">
        <h3>整票撤销</h3>
        <div class="note bad">撤销后该小票退出重算样本；之后晚到的旧版本更正会被乐观锁拒绝，无法把票加回来。</div>
        ${ticketForm(ctx, 'void')}
      </div>
      <div class="panel span-4">
        <h3>补传票批次 / ${batches.length}</h3>
        <div class="table-wrap"><table class="evidence-table"><thead><tr>
          <th>批次号</th><th>操作</th><th>票数</th><th>样本</th>
        </tr></thead><tbody>
        ${batches.map(b => `<tr>
          <td class="mono">${escapeHtml(b.batch_no)}</td>
          <td>${opLabels[b.op] || b.op}</td>
          <td>${b.ticket_count}</td>
          <td><span class="version-chip">v${b.base_version}→v${b.new_version}</span></td>
        </tr>`).join('') || '<tr><td colspan="4" style="color:var(--muted)">暂无补传批次</td></tr>'}
        </tbody></table></div>
      </div>
      <div class="panel span-12">
        <h3>购物篮小票 / ${orders.length}</h3>
        <div class="table-wrap"><table><thead><tr>
          <th>外部订单号</th><th>门店</th><th>金额</th><th>明细数</th><th>版本</th>
          <th>状态</th><th>最近批次</th><th>留痕</th>
        </tr></thead><tbody>
        ${orders.map(o => `<tr>
          <td class="mono">${escapeHtml(o.external_id)}</td>
          <td>${escapeHtml(o.store_name)}<br><span class="kicker">${escapeHtml(o.store_code)}</span></td>
          <td>¥${Number(o.total_amount).toFixed(2)}</td>
          <td>${o.item_count}</td>
          <td><span class="version-chip">v${o.version}</span></td>
          <td><span class="status status-${o.status === 'voided' ? 'rejected' : 'approved'}">
            ${o.status === 'voided' ? '已撤销' : '完整入篮'}</span></td>
          <td class="mono" style="font-size:11px">${escapeHtml(o.latest_batch_no || '')}</td>
          <td><button class="btn alt small ticket-history" data-ticket="${escapeHtml(o.external_id)}">版本留痕</button></td>
        </tr>`).join('')}
        </tbody></table></div>
      </div>
    </div>`;
  ctx.shell(content);

  for (const kind of ['correct', 'void']) {
    const form = document.getElementById(`${kind}-form`);
    form.onsubmit = async event => {
      event.preventDefault();
      const alert = form.querySelector('[role="alert"]');
      alert.hidden = true;
      try {
        const ticket = readForm(form, kind);
        const payload = {
          batch_no: ticket.batch_no, source_file: ticket.source_file, note: ticket.note,
          tickets: [ticket],
        };
        const result = await ctx.api(kind === 'void' ? '/orders/voids' : '/orders/corrections', {
          method: 'POST', body: JSON.stringify(payload),
        });
        if (result.idempotent) {
          ctx.toast('该批次号已处理过，按幂等直接返回，未重复入篮');
        } else {
          const invalidated = result.invalidated_rule_ids.length;
          ctx.toast(`已受理，样本 v${result.base_version}→v${result.new_version}`
            + (invalidated ? `，${invalidated} 条旧样本候选已失效` : ''));
        }
        ctx.refresh();
      } catch (error) {
        alert.textContent = error.message;
        alert.hidden = false;
      }
    };
  }
  document.querySelectorAll('.ticket-history').forEach(btn => {
    btn.onclick = () => showHistory(ctx, btn.dataset.ticket);
  });
}
