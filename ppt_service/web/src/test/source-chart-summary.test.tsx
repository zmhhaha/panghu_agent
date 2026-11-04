import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { DirectionChooser } from '../DirectionChooser';
import { SourceChartSummary } from '../SourceChartSummary';
import { adaptDirections } from '../planning';

function sourceDirection() {
 return {id:'source-chart',name:'资料图表方案',note:'保留原始单位',
  page_plan:{pages:[{ordinal:1,title:'数据说明',purpose:'核对事实'}]},production_parameters:{page_count:6},
  native_chart_specs:[{table_id:'internal-table-id',category_column:0,series_columns:[1],slide:4,type:'column'}],
  source_chart_binding:{schema_version:1,direction_id:'source-chart',binding_sha256:'private-digest',
   charts:[{asset_id:'source',asset_sha256:'private-source-hash',table_id:'internal-table-id',line_start:12,
    slide:4,type:'column',category_column:0,series_columns:[1],headers:['年份','温度距平（摄氏度）'],
    rows:[['2019','0.98'],['2020','1.01'],['2021','0.85'],['2022','0.89'],['2023','1.17'],['2024','1.28']]}]}};
}

describe('source data in the existing direction confirmation',()=>{
 it('shows exact source rows and units for the selected direction without another checkbox or action',()=>{
  const direction=adaptDirections([sourceDirection()])![0];
  const onSelect=vi.fn();
  render(<DirectionChooser directions={[direction]} selectedId={direction.id} onSelect={onSelect} disabled={false}
   assets={[{id:'source',display_name:'公开资料.md',bytes:100,parse_status:'ready'}]} renderDetails={()=>null}/>);
  const summary=within(screen.getByRole('region',{name:'所选方案的图表数据'}));
  expect(summary.getByText('第 4 页 · 可编辑柱状图')).toBeVisible();
  expect(summary.getByText('来源：公开资料.md')).toBeVisible();
  expect(summary.getByRole('columnheader',{name:'温度距平（摄氏度）'})).toBeVisible();
  expect(summary.getAllByRole('row')).toHaveLength(7);
  for(const [year,value] of sourceDirection().source_chart_binding.charts[0].rows){
   expect(summary.getByRole('cell',{name:year})).toBeVisible();expect(summary.getByRole('cell',{name:value})).toBeVisible();
  }
  expect(summary.queryByRole('checkbox')).not.toBeInTheDocument();expect(summary.queryByRole('button')).not.toBeInTheDocument();
  expect(onSelect).not.toHaveBeenCalled();
  expect(document.body.textContent).not.toMatch(/private-digest|private-source-hash|internal-table-id/);
  expect(direction.details).not.toHaveProperty('source_chart_binding');expect(direction.details).not.toHaveProperty('native_chart_specs');
 });

 it('makes the full source rows available without changing the selected plan',async()=>{
  const raw=sourceDirection();raw.source_chart_binding.charts[0].rows.push(['2025','1.30']);
  const direction=adaptDirections([raw])![0];
  render(<SourceChartSummary direction={direction}/>);
  expect(screen.getByText('7 行数据 · 1 组数值')).toBeVisible();
  expect(screen.getByRole('cell',{name:'2025',hidden:true})).not.toBeVisible();
  await userEvent.click(screen.getByText('查看全部 7 行数据',{selector:'summary'}));
  expect(screen.getByRole('cell',{name:'2025'})).toBeVisible();
  expect(screen.getByRole('cell',{name:'1.30'})).toBeVisible();
 });

 it('keeps chart-free legacy directions quiet and refuses malformed or missing chart data',()=>{
  const legacy=adaptDirections([{id:'legacy',name:'文字稿',page_plan:[],production_parameters:{}}])![0];
  const view=render(<SourceChartSummary direction={legacy}/>);
  expect(screen.queryByRole('region')).not.toBeInTheDocument();expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  const missing=sourceDirection();delete (missing as Partial<typeof missing>).source_chart_binding;
  view.rerender(<SourceChartSummary direction={adaptDirections([missing])![0]}/>);
  expect(screen.getByRole('alert')).toHaveTextContent('图表数据尚未准备好');
  const invalid=sourceDirection();invalid.source_chart_binding.charts[0].series_columns=[8];
  view.rerender(<SourceChartSummary direction={adaptDirections([invalid])![0]}/>);
  expect(screen.queryByRole('table')).not.toBeInTheDocument();expect(screen.getByRole('alert')).toBeVisible();
  const empty=sourceDirection();empty.source_chart_binding.charts=[];
  view.rerender(<SourceChartSummary direction={adaptDirections([empty])![0]}/>);
  expect(screen.getByRole('alert')).toHaveTextContent('图表数据尚未准备好');
 });

 it('renders passive data as escaped text and never a resource or HTML node',()=>{
  const raw=sourceDirection();raw.source_chart_binding.charts[0].rows[0][0]='<img src=x onerror=alert(1)>';
  render(<SourceChartSummary direction={adaptDirections([raw])![0]}/>);
  expect(screen.getByRole('cell',{name:'<img src=x onerror=alert(1)>'})).toBeVisible();
  expect(document.querySelector('img')).toBeNull();expect(screen.queryByRole('link')).not.toBeInTheDocument();
 });
});
