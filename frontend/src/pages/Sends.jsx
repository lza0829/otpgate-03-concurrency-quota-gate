import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Sends() {
  const [rows, setRows] = useState([])
  const [slots, setSlots] = useState([])
  const [rejections, setRejections] = useState([])
  const [phone, setPhone] = useState('139****6602')
  const [scene, setScene] = useState('登录')
  const [msg, setMsg] = useState('')
  const [bad, setBad] = useState(false)
  const load = async () => {
    try {
      const [sends, slotView, rej] = await Promise.all([
        api('/投递单'), api('/槽位'), api('/拒投记录'),
      ])
      setRows(sends)
      setSlots(slotView.槽位 || [])
      setRejections(rej || [])
      setBad(false)
    } catch (e) { setMsg(e.message); setBad(true) }
  }
  useEffect(() => { load() }, [])
  const create = async () => {
    try {
      const r = await api('/投递单', { method: 'POST', body: JSON.stringify({ 手机掩码: phone, 场景: scene }) })
      setMsg(`已创建｜${r.通道} 槽位 ${r.槽位.在途占用}/${r.槽位.槽位上限}`)
      setBad(false)
      load()
    } catch (e) { setMsg(e.message); setBad(true) }
  }
  const dispatch = async (id) => {
    try {
      const r = await api(`/投递单/${id}/投递`, { method: 'POST' })
      setMsg(`投递成功：${r.通道}｜在途 ${r.槽位.在途占用}/${r.槽位.槽位上限}，剩余 ${r.槽位.剩余名额}`)
      setBad(false)
      load()
    } catch (e) {
      // 满槽/不可投递：整笔拒收，单据保持原状态，名额未变
      setMsg(e.message)
      setBad(true)
      load()
    }
  }
  return (
    <div className="panel">
      <h2>验码单 · 投递事务匣</h2>
      <div className="slot-bar">
        {slots.map((s) => (
          <div key={s.通道编号} className={`slot-chip ${s.满槽 ? 'chip-full' : 'chip-free'}`}>
            <span className="chip-name">{s.通道}</span>
            <span className="chip-num">{s.在途占用}/{s.槽位上限}</span>
            <span className="chip-lamp">{s.满槽 ? '满格·拒投' : `余 ${s.剩余名额}`}</span>
          </div>
        ))}
      </div>
      <div className="row">
        <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="手机掩码" />
        <select value={scene} onChange={(e) => setScene(e.target.value)}>
          <option value="登录">登录</option>
          <option value="支付">支付</option>
          <option value="改密">改密</option>
        </select>
        <button type="button" onClick={create}>新建投递单</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>投递单号</th><th>手机掩码</th><th>场景</th><th>状态</th><th>通道</th><th>切备通道</th><th>操作</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号}>
                <td>{r.投递单号}</td><td>{r.手机掩码}</td><td>{r.场景}</td><td>{r.状态}</td><td>{r.通道}</td><td>{r.切备通道}</td>
                <td><button type="button" onClick={() => dispatch(r.编号)} disabled={!['待投递','已切备'].includes(r.状态)}>投递</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><span className={`msg ${bad ? 'bad' : 'ok'}`}>{msg}</span><button type="button" className="secondary" onClick={load}>刷新</button></div>
      <h2>满槽拒投留痕</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>投递单</th><th>通道</th><th>拒因</th><th>在途/上限</th><th>时间</th></tr></thead>
          <tbody>
            {rejections.length === 0 && <tr><td colSpan={5} className="msg">暂无拒投（无超槽、无假成功占用）</td></tr>}
            {rejections.map((r) => (
              <tr key={r.编号}><td>{r.投递单}</td><td>{r.通道}</td><td className="bad">{r.拒因}</td><td>{r.在途占用}/{r.槽位上限}</td><td>{r.时间}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
