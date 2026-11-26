import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Reconcile() {
  const [data, setData] = useState(null)
  const [tickets, setTickets] = useState([])
  const [err, setErr] = useState('')
  const load = async () => {
    try {
      const [rec, ovf] = await Promise.all([api('/对账'), api('/溢出票')])
      setData(rec)
      setTickets(ovf.溢出票 || [])
    } catch (e) { setErr(e.message) }
  }
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const top = Object.fromEntries(Object.entries(data).filter(([, v]) => !Array.isArray(v)))
  return (
    <div className="panel">
      <h2>轧差</h2>
      <div className="grid">
        {Object.entries(top).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{String(v)}</div><div className="l">{k}</div></div>
        ))}
      </div>
      <h2>路况在途桶（按通道）</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>通道</th><th>槽位上限</th><th>在途占用</th><th>剩余名额</th><th>判定</th></tr></thead>
          <tbody>
            {(data.路况在途桶 || []).map((b) => (
              <tr key={b.通道编号} className={b.在途占用 > b.槽位上限 ? 'row-full' : ''}>
                <td>{b.通道}</td><td>{b.槽位上限}</td><td><strong>{b.在途占用}</strong></td><td>{b.剩余名额}</td>
                <td>{b.在途占用 > b.槽位上限
                  ? <span className="bad">超槽事故</span>
                  : b.在途占用 === b.槽位上限 ? <span className="slot-lamp lamp-full">满格</span> : <span className="ok">正常</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>轧差溢出桶 · 溢出票</h2>
      <p className="msg">口径 {data.槽位口径}：溢出桶 {data.轧差溢出桶}、溢出在途 {data.溢出在途}。硬闸口径下溢出票恒为空，空表即「无超槽投递」可核证据。</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>编号</th><th>投递单</th><th>通道</th><th>出票时在途</th><th>槽位上限</th><th>时间</th></tr></thead>
          <tbody>
            {tickets.length === 0 && <tr><td colSpan={6} className="msg">无溢出票（零溢出口径生效中）</td></tr>}
            {tickets.map((t) => (
              <tr key={t.编号}><td>{t.编号}</td><td>{t.投递单}</td><td>{t.通道}</td><td>{t.出票时在途}</td><td>{t.槽位上限}</td><td>{t.时间}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
