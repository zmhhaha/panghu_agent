import type { Asset, Direction, SourceChartData } from './domain';
import './source-chart-summary.css';

const chartTypes={column:'柱状图',line:'折线图',pie:'饼图'};

function DataTable({chart,rows}:{chart:SourceChartData;rows:string[][]}) {
 const columns=[chart.category_column,...chart.series_columns];
 return <div className="source-chart-table"><table><caption className="sr-only">第 {chart.slide} 页的图表源数据</caption><thead><tr>{columns.map(column=><th key={column} scope="col">{chart.headers[column]}</th>)}</tr></thead><tbody>{rows.map((row,index)=><tr key={index}>{columns.map(column=><td key={column}>{row[column]}</td>)}</tr>)}</tbody></table></div>;
}

export function SourceChartSummary({direction,assets=[]}:{direction:Direction;assets?:Asset[]}) {
 if(direction.source_chart_error)return <div className="notice warning" role="alert">图表数据尚未准备好，请重新生成设计方案后再确认。</div>;
 const charts=direction.source_charts;
 if(!charts?.length)return null;
 return <section className="source-chart-summary" aria-label="所选方案的图表数据"><div className="source-chart-heading"><h4>这份方案的图表将使用这些数据</h4><p>确认方案时，一并确认下方的图表安排和资料数值。</p></div>{charts.map((chart,index)=>{
  const source=assets.find(asset=>asset.id===chart.asset_id)?.display_name??'已上传资料';
  return <article className="source-chart-item" key={index} aria-label={`第 ${chart.slide} 页${chartTypes[chart.type]}`}><div className="source-chart-title"><strong>第 {chart.slide} 页 · 可编辑{chartTypes[chart.type]}</strong><span>{chart.rows.length} 行数据 · {chart.series_columns.length} 组数值</span></div><p className="source-chart-origin">来源：{source}</p><DataTable chart={chart} rows={chart.rows.slice(0,6)}/>{chart.rows.length>6&&<details><summary>查看全部 {chart.rows.length} 行数据</summary><DataTable chart={chart} rows={chart.rows}/></details>}</article>;
 })}</section>;
}
