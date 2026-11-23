// Canonical per-realm numeric allowance. Geometry helper URL fragments all
// import this exact dependency. Reservations cannot be reset or refunded.
export const MAX_GEOMETRY_RETAINED_BYTES=16*1024*1024;
export const MAX_GEOMETRY_HANDLES=32;
let handlesReserved=0,numericBytesReserved=0,meshesCreated=0,projectionsCreated=0;

export function geometryResourceUsage(){
 return Object.freeze({meshes_created:meshesCreated,projections_created:projectionsCreated,
  handles_reserved:handlesReserved,numeric_bytes_reserved:numericBytesReserved,
  max_handles:MAX_GEOMETRY_HANDLES,max_total_bytes:MAX_GEOMETRY_RETAINED_BYTES});
}

export function reserveGeometryBytes(kind,bytes){
 if(!['mesh','projection'].includes(kind)||!Number.isSafeInteger(bytes)||bytes<=0||bytes>MAX_GEOMETRY_RETAINED_BYTES)
  throw Error('scene_geometry_reservation_invalid');
 if(handlesReserved>=MAX_GEOMETRY_HANDLES||numericBytesReserved+bytes>MAX_GEOMETRY_RETAINED_BYTES)
  throw Error('scene_geometry_retained_budget');
 handlesReserved++;numericBytesReserved+=bytes;
 let completed=false;
 return()=>{if(completed)throw Error('scene_geometry_commit_repeated');completed=true;
  if(kind==='mesh')meshesCreated++;else projectionsCreated++;};
}
