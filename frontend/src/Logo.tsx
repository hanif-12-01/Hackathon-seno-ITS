/** User-supplied brand mark, served locally without altering the original artwork. */
export function Logo({alt = ''}: {alt?: string}) {
  return (
    <img
      className="logo"
      src="/brand/pangannadi-logo-96.webp"
      srcSet="/brand/pangannadi-logo-96.webp 1x, /brand/pangannadi-logo-192.webp 2x"
      alt={alt}
      width={48}
      height={48}
      decoding="async"
    />
  );
}
