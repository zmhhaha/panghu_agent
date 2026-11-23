// A canonical dependency, deliberately separate from the drawing helper.
// helper.mjs#one and helper.mjs#two both import this exact URL and therefore
// consume one realm's allowance. No source-accessible reset, free or raise API.
export const MAX_TOTAL_FIELD_BYTES=16*1024*1024;
export const MAX_FIELDS=64;
let fieldsReserved=0,fieldsCreated=0,numericBytesReserved=0;

export function surfaceResourceUsage(){
 return Object.freeze({fields_created:fieldsCreated,fields_reserved:fieldsReserved,
  numeric_bytes_reserved:numericBytesReserved,max_fields:MAX_FIELDS,
  max_total_field_bytes:MAX_TOTAL_FIELD_BYTES});
}

export function reserveFieldBytes(bytes){
 if(typeof bytes!=='number'||!(bytes>0&&bytes<=MAX_TOTAL_FIELD_BYTES)||bytes%1!==0)
  throw Error('scene_surface_field_reservation_invalid');
 if(fieldsReserved>=MAX_FIELDS||numericBytesReserved+bytes>MAX_TOTAL_FIELD_BYTES)
  throw Error('scene_surface_retained_budget');
 // Charge before allocation. A later allocation/computation failure keeps the
 // reservation; GC timing is never treated as proof of released storage.
 fieldsReserved++;numericBytesReserved+=bytes;
 let completed=false;
 return()=>{if(completed)throw Error('scene_surface_field_commit_repeated');
  completed=true;fieldsCreated++;};
}
