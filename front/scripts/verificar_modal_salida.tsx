/**
 * El boton de "Salir" Se mira desde fuera, sin navegador.
 *
 * `tsc` pasa con un boton que no pregunta nada y con un modal que dice "no se
 * puede salir" pero deja el boton activo. Por eso este script monta la vista del
 * modal en sus cuatro estados y mira **el HTML**, no el retorno de una funcion.
 *
 * Reglas que protege:
 *
 *  1. El clic en "Salir" de la barra abre una confirmacion; no cierra la sesion.
 *  2. Mientras se comprueba, con corrida en curso o con error de red, el boton
 *     de salir esta deshabilitado. Solo el estado "libre" lo habilita.
 *  3. Con digitalizacion en curso se dice cuantas y se ofrece recompobar.
 *  4. Cancelar siempre funciona, incluso cuando no se puede salir.
 *
 * Uso, desde front/:  npm run verify:salida
 */
import { readFileSync } from 'fs';
import { renderToStaticMarkup } from 'react-dom/server';
import React from 'react';
import { ModalSalirVista, cuantasEnCurso } from '../src/components/ModalSalir';

const fallos: string[] = [];
function comprobar(nombre: string, condicion: boolean, detalle = ''): void {
  console.log(`  ${condicion ? 'OK   ' : 'FALLA'} ${nombre}${!condicion && detalle ? `\n         ${detalle}` : ''}`);
  if (!condicion) fallos.push(nombre);
}

/** El `<button ...>` que contiene un texto, para mirar su `disabled`. */
function botonConTexto(html: string, texto: string): string {
  const fin = html.indexOf(`>${texto}</button>`);
  if (fin < 0) return '';
  const inicio = html.lastIndexOf('<button', fin);
  return html.slice(inicio, fin + texto.length + 10);
}

/** `disabled:` en las clases de Tailwind NO es el atributo. Mirar solo la
 *  palabra daba verde con el boton activo: las clases estan en los cuatro
 *  estados. */
function estaDeshabilitado(boton: string): boolean {
  return /(^|\s)disabled(?:="")?(?=\s|>)/.test(boton);
}

function vista(estado: 'comprobando' | 'libre' | 'ocupado' | 'error', enCurso = 0): string {
  return renderToStaticMarkup(
    React.createElement(ModalSalirVista, {
      estado,
      enCurso,
      onCancelar: () => undefined,
      onConfirmar: () => undefined,
      onRevisar: () => undefined,
    }),
  );
}

const comprobando = vista('comprobando');
const libre = vista('libre');
const ocupado = vista('ocupado', 2);
const error = vista('error');

console.log('\nEl modal de salida:');

comprobar('la pregunta se ve en los cuatro estados',
  [comprobando, libre, ocupado, error].every((h) => h.includes('Desea salir de la aplicacion?')));

comprobar('comprobando bloquea la salida',
  estaDeshabilitado(botonConTexto(comprobando, 'Salir')));
comprobar('ocupado bloquea la salida',
  estaDeshabilitado(botonConTexto(ocupado, 'Salir')));
comprobar('error de red bloquea la salida',
  estaDeshabilitado(botonConTexto(error, 'Salir')));
comprobar('libre SI deja salir',
  !estaDeshabilitado(botonConTexto(libre, 'Salir')) &&
  botonConTexto(libre, 'Salir').includes('>Salir</button>'));

comprobar('la corrida en curso se dice con su numero',
  ocupado.includes('No se puede salir') && ocupado.includes('2 digitalizacion(es) en curso'));
comprobar('el aviso de corrida es un alert',
  ocupado.includes('role="alert"'));
comprobar('con error se dice que no se pudo comprobar',
  error.includes('No se pudo comprobar'));
comprobar('se puede volver a comprobar solo cuando hay bloqueo',
  ocupado.includes('Volver a comprobar') && error.includes('Volver a comprobar') &&
  !libre.includes('Volver a comprobar'));
comprobar('cancelar sigue disponible siempre',
  [comprobando, libre, ocupado, error].every((h) =>
    botonConTexto(h, 'Cancelar').includes('>Cancelar</button>') &&
    !estaDeshabilitado(botonConTexto(h, 'Cancelar'))));

comprobar('la verdad la da en_curso del servidor',
  cuantasEnCurso(['a', 'b']) === 2 && cuantasEnCurso([]) === 0);

console.log('\nEl boton de la barra y la defensa del confirmar:');

const app = readFileSync('src/App.tsx', 'utf8');
const componente = readFileSync('src/components/ModalSalir.tsx', 'utf8');
comprobar('el boton de la barra abre el modal, no cierra la sesion',
  /onClick=\{\(\) => setConfirmarSalida\(true\)\}/.test(app) &&
  !/onClick=\{\(\) => \{ void cerrarSesion\(\); \}\}/.test(app));
comprobar('la confirmacion pregunta al servidor por las corridas',
  componente.includes('ticketsApi.scanRuns()'));
comprobar('una corrida viva se declara ocupada, no libre',
  componente.includes("setEstado(n > 0 ? 'ocupado' : 'libre')"));
comprobar('un error de red no se finge libre',
  componente.includes("setEstado('error')"));
comprobar('confirmar exige el estado libre aunque lo dispare codigo',
  componente.includes("if (estado !== 'libre') return;"));

console.log(`\n${fallos.length === 0 ? 'TODO OK' : `${fallos.length} FALLOS`}`);
process.exit(fallos.length === 0 ? 0 : 1);
