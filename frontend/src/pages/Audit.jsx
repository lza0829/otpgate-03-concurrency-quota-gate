import React, { useEffect, useState } from 'react'
import { api } from '../api'

const ACTIONS = ['', '拒投', '投递', '创建', '切备', '熔断', '恢复', '回执', '失败', '变更']

export default function Audit() {
  const [rows, setRows] = useState([])
  const [动作, set动作] = useState('')
  const [err, setErr] = useState('')
  const load = (action = '') => {
    const qs = action ? `?动作=${encodeURIComponent(action)}` : ''
    api(`/审计${qs}`).then(setRows).catch((e) => setErr(e.message))
  }
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  return (
    <div className="panel">
      <h2>事件痕</h2>
      <div className="row">
        <span className="msg">动作过滤：</span>
        <select value={动作} onChange={(e) => { set动作(e.target.value); load(e.target.value) }}>
          {ACTIONS.map((a) => <option key={a} value={a}>{a || '全部'}</option>)}
        </select>
        <button type="button" className="secondary" onClick={() => { set动作('拒投'); load('拒投') }}>只看槽位闸拒投</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>类别</th><th>关联</th><th>动作</th><th>时间</th><th>备注</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} className={r.动作 === '拒投' ? 'row-full' : ''}>
                <td>{r.类别}</td><td>{r.关联 ?? '-'}</td><td>{r.动作}</td><td>{r.时间}</td><td>{r.备注}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={() => load(动作)}>刷新</button></div>
    </div>
  )
}
