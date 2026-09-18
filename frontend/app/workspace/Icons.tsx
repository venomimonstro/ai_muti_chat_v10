import type {SVGProps} from "react";

export type IconName="panel"|"plus"|"search"|"folder"|"folderPlus"|"pin"|"more"|"wallet"|"settings"|"user"|"send"|"stop"|"copy"|"pencil"|"retry"|"trash"|"chevron"|"spark"|"zap"|"scale"|"brain"|"check"|"x"|"arrowDown";

const paths:Record<IconName,string>={
 panel:"M4 5h16v14H4zM9 5v14",
 plus:"M12 5v14M5 12h14",
 search:"m21 21-4.35-4.35M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z",
 folder:"M3 6h7l2 2h9v10H3z",
 folderPlus:"M3 7h7l2 2h9v9H3zM12 11v5M9.5 13.5h5",
 pin:"m9 4 6 0 1 5 3 3-5 1-2 7-2-7-5-1 3-3z",
 more:"M5 12h.01M12 12h.01M19 12h.01",
 wallet:"M4 7h15v11H4zM4 9V6h12M15 12h4",
 settings:"M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm8 3 2-1-2-3-2 .3-1-1.7.3-2-3-2-1.7-2 1-3-.3-2 3 .3 2-1 1.7-2 3 2 1.7 1 2 3 2 1 1-2 .3-2-3z",
 user:"M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 8c.7-4 3-6 7-6s6.3 2 7 6",
 send:"M4 12 20 4l-6 16-2-7z",
 stop:"M7 7h10v10H7z",
 copy:"M9 9h10v10H9zM5 5h10v4M5 5v10h4",
 pencil:"m4 20 4-1 11-11-3-3L5 16zM14 6l3 3",
 retry:"M20 11a8 8 0 1 0-2 5M20 4v7h-7",
 trash:"M5 7h14M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5",
 chevron:"m9 18 6-6-6-6",
 spark:"m12 2 1.6 5.4L19 9l-5.4 1.6L12 16l-1.6-5.4L5 9l5.4-1.6z",
 zap:"m13 2-7 11h6l-1 9 7-12h-6z",
 scale:"M12 3v18M5 7h14M7 7l-4 7h8zM17 7l-4 7h8z",
 brain:"M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0-1 5 3 3 0 0 0 2 5 3 3 0 0 0 5 2V6a2 2 0 0 0-3-2Zm6 0a3 3 0 0 1 3 3v1a3 3 0 0 1 1 5 3 3 0 0 1-2 5 3 3 0 0 1-5 2V6a2 2 0 0 1 3-2Z",
 check:"m5 12 4 4L19 6",
 x:"M6 6l12 12M18 6 6 18",
 arrowDown:"M12 5v14m-6-6 6 6 6-6",
};

export function Icon({name,size=18,...props}:{name:IconName;size?:number}&SVGProps<SVGSVGElement>){return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}><path d={paths[name]}/></svg>}
