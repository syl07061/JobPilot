const paths = {
  brand: 'M3 17 11 3l3 8 7 3-14 7 2-5-6 1Z',
  filters: 'M4 6h16M4 12h16M4 18h16M8 4v4m8 2v4m-6 2v4',
  results: 'M5 5h14v14H5zM8 9h8m-8 3h8m-8 3h5',
  resumes: 'M6 3h9l3 3v15H6zM14 3v4h4M9 16h6m-6 2h6m-2-7a1 1 0 1 0-2 0 1 1 0 0 0 2 0Z',
  review: 'm12 3 2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2L12 3Z',
  config: 'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3Zm6 9a3 3 0 1 0-6 0 3 3 0 0 0 6 0Z',
} as const

export default function WorkspaceIcon({ name }: { name: keyof typeof paths }) {
  return <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d={paths[name]}/></svg>
}
