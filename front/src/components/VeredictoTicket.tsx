import { veredictoDeTicket, lineaVeredicto, TONOS } from '../utils/veredicto';
import type { Ticket } from '../types/api';

/**
 * Enseña el veredicto del confidence gate.
 *
 * Existe porque el dato mas importante del sistema no se mostraba en ningun
 * lado: un ticket podia haberse auto-aprobado sin que nadie lo supiera, y eso
 * hacia indistinguible "el sistema lo verifico" de "nadie lo reviso".
 *
 * No recibe props de veredicto ya armado: toma el ticket y lo descompone con
 * `veredictoDeTicket`, para que ninguna pantalla pueda mostrarse a si misma
 * un estado inventado.
 */
export function VeredictoTicket({ ticket }: { ticket: Ticket }) {
  const v = veredictoDeTicket(ticket);
  const linea = lineaVeredicto(v);

  return (
    <div className={`border px-4 py-3 rounded-lg ${TONOS[linea.tono]}`}>
      <p className="font-medium text-sm">{linea.titulo}</p>
      <p className="text-sm mt-0.5">{linea.detalle}</p>
    </div>
  );
}
