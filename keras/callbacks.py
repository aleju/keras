from __future__ import absolute_import
from __future__ import print_function
import theano
import theano.tensor as T
import numpy as np
import warnings
import time, json
from collections import deque

from .utils.generic_utils import Progbar
from .utils.plotting_utils import PlotGenerator

class CallbackList(object):

    def __init__(self, callbacks=[], queue_length=10):
        self.callbacks = [c for c in callbacks]
        self.queue_length = queue_length

    def append(self, callback):
        self.callbacks.append(callback)

    def _set_params(self, params):
        for callback in self.callbacks:
            callback._set_params(params)

    def _set_model(self, model):
        for callback in self.callbacks:
            callback._set_model(model)

    def on_epoch_begin(self, epoch, logs={}):
        for callback in self.callbacks:
            callback.on_epoch_begin(epoch, logs)
        self._delta_t_batch = 0.
        self._delta_ts_batch_begin = deque([], maxlen=self.queue_length)
        self._delta_ts_batch_end = deque([], maxlen=self.queue_length)

    def on_epoch_end(self, epoch, logs={}):
        for callback in self.callbacks:
            callback.on_epoch_end(epoch, logs)

    def on_batch_begin(self, batch, logs={}):
        t_before_callbacks = time.time()
        for callback in self.callbacks:
            callback.on_batch_begin(batch, logs)
        self._delta_ts_batch_begin.append(time.time() - t_before_callbacks)
        delta_t_median = np.median(self._delta_ts_batch_begin)
        if self._delta_t_batch > 0. and delta_t_median > 0.95 * self._delta_t_batch \
            and delta_t_median > 0.1:
            warnings.warn('Method on_batch_begin() is slow compared '
                'to the batch update (%f). Check your callbacks.' % delta_t_median)
        self._t_enter_batch = time.time()

    def on_batch_end(self, batch, logs={}):
        self._delta_t_batch = time.time() - self._t_enter_batch
        t_before_callbacks = time.time()
        for callback in self.callbacks:
            callback.on_batch_end(batch, logs)
        self._delta_ts_batch_end.append(time.time() - t_before_callbacks)
        delta_t_median = np.median(self._delta_ts_batch_end)
        if self._delta_t_batch > 0. and delta_t_median > 0.95 * self._delta_t_batch \
            and delta_t_median > 0.1:
            warnings.warn('Method on_batch_end() is slow compared '
                'to the batch update (%f). Check your callbacks.' % delta_t_median)

    def on_train_begin(self, logs={}):
        for callback in self.callbacks:
            callback.on_train_begin(logs)

    def on_train_end(self, logs={}):
        for callback in self.callbacks:
            callback.on_train_end(logs)


class Callback(object):

    def __init__(self):
        pass

    def _set_params(self, params):
        self.params = params

    def _set_model(self, model):
        self.model = model

    def on_epoch_begin(self, epoch, logs={}):
        pass

    def on_epoch_end(self, epoch, logs={}):
        pass

    def on_batch_begin(self, batch, logs={}):
        pass

    def on_batch_end(self, batch, logs={}):
        pass

    def on_train_begin(self, logs={}):
        pass

    def on_train_end(self, logs={}):
        pass

class BaseLogger(Callback):

    def on_train_begin(self, logs={}):
        self.verbose = self.params['verbose']

    def on_epoch_begin(self, epoch, logs={}):
        if self.verbose:
            print('Epoch %d' % epoch)
            self.progbar = Progbar(target=self.params['nb_sample'], \
                verbose=self.verbose)
        self.current = 0
        self.tot_loss = 0.
        self.tot_acc = 0.

    def on_batch_begin(self, batch, logs={}):
        if self.current < self.params['nb_sample']:
            self.log_values = []

    def on_batch_end(self, batch, logs={}):
        batch_size = logs.get('size', 0)
        self.current += batch_size

        loss = logs.get('loss')
        self.log_values.append(('loss', loss))
        self.tot_loss += loss * batch_size
        if self.params['show_accuracy']:
            accuracy = logs.get('accuracy')
            self.log_values.append(('acc.', accuracy))
            self.tot_acc += accuracy * batch_size
        # skip progbar update for the last batch; will be handled by on_epoch_end
        if self.verbose and self.current < self.params['nb_sample']:
            self.progbar.update(self.current, self.log_values)

    def on_epoch_end(self, epoch, logs={}):
        self.log_values.append(('loss', self.tot_loss / self.current))
        if self.params['show_accuracy']:
            self.log_values.append(('acc.', self.tot_acc / self.current))
        if self.params['do_validation']:
            val_loss = logs.get('val_loss')
            self.log_values.append(('val. loss', val_loss))
            if self.params['show_accuracy']:
                val_acc = logs.get('val_accuracy')
                self.log_values.append(('val. acc.', val_acc))
        self.progbar.update(self.current, self.log_values)


class History(Callback):

    def on_train_begin(self, logs={}):
        self.epoch = []
        self.loss = []
        if self.params['show_accuracy']:
            self.accuracy = []
        if self.params['do_validation']:
            self.validation_loss = []
            if self.params['show_accuracy']:
                self.validation_accuracy = []

    def on_epoch_begin(self, epoch, logs={}):
        self.seen = 0
        self.tot_loss = 0.
        self.tot_accuracy = 0.

    def on_batch_end(self, batch, logs={}):
        batch_size = logs.get('size', 0)
        self.seen += batch_size
        self.tot_loss += logs.get('loss', 0.) * batch_size
        if self.params['show_accuracy']:
            self.tot_accuracy += logs.get('accuracy', 0.) * batch_size

    def on_epoch_end(self, epoch, logs={}):
        val_loss = logs.get('val_loss')
        val_acc = logs.get('val_accuracy')
        self.epoch.append(epoch)
        self.loss.append(self.tot_loss / self.seen)
        if self.params['show_accuracy']:
            self.accuracy.append(self.tot_accuracy / self.seen)
        if self.params['do_validation']:
            self.validation_loss.append(val_loss)
            if self.params['show_accuracy']:
                self.validation_accuracy.append(val_acc)

class Plotter(History):
    # see PlotGenerator.__init__() for a description of the parameters
    def __init__(self,
                 save_to_filepath=None, show_plot_window=True,
                 linestyles=None, linestyles_first_epoch=None,
                 show_regressions=True,
                 poly_forward_perc=0.1, poly_backward_perc=0.2,
                 poly_n_forward_min=5, poly_n_backward_min=10,
                 poly_degree=1):
        super(Plotter, self).__init__()
        pgen = PlotGenerator(linestyles=linestyles,
                             linestyles_first_epoch=linestyles_first_epoch,
                             show_regressions=show_regressions,
                             poly_forward_perc=poly_forward_perc,
                             poly_backward_perc=poly_backward_perc,
                             poly_n_forward_min=poly_n_forward_min,
                             poly_n_backward_min=poly_n_backward_min,
                             poly_degree=poly_degree,
                             show_plot_window=show_plot_window,
                             save_to_filepath=save_to_filepath)
        self.plot_generator = pgen

    def on_epoch_end(self, epoch, logs={}):
        super(Plotter, self).on_epoch_end(epoch, logs)
        dv = self.params['do_validation']
        sa = self.params['show_accuracy']

        train_loss = self.loss
        val_loss = self.validation_loss if dv else []
        train_acc = self.accuracy if sa else []
        val_acc = self.validation_accuracy if dv and sa else []

        self.plot_generator.update(epoch, train_loss, train_acc,
                                   val_loss, val_acc)

class ModelCheckpoint(Callback):
    def __init__(self, filepath, verbose=0, save_best_only=False):
        super(Callback, self).__init__()
        
        self.verbose = verbose
        self.filepath = filepath
        self.save_best_only = save_best_only
        self.loss = []
        self.best_loss = np.Inf
        self.val_loss = []
        self.best_val_loss = np.Inf

    def on_epoch_end(self, epoch, logs={}):
        if self.save_best_only and self.params['do_validation']:
            cur_val_loss = logs.get('val_loss')
            self.val_loss.append(cur_val_loss)
            if cur_val_loss < self.best_val_loss:
                if self.verbose > 0:
                    print("Epoch %05d: validation loss improved from %0.5f to %0.5f, saving model to %s"
                        % (epoch, self.best_val_loss, cur_val_loss, self.filepath))
                self.best_val_loss = cur_val_loss
                self.model.save_weights(self.filepath, overwrite=True)
            else:
                if self.verbose > 0:
                    print("Epoch %05d: validation loss did not improve" % (epoch))
        elif self.save_best_only and not self.params['do_validation']:
            warnings.warn("Can save best model only with validation data, skipping", RuntimeWarning)
        elif not self.save_best_only:
            if self.verbose > 0:
                print("Epoch %05d: saving model to %s" % (epoch, self.filepath))
            self.model.save_weights(self.filepath, overwrite=True)


class EarlyStopping(Callback):
    def __init__(self, patience=0, verbose=0):
        super(Callback, self).__init__()

        self.patience = patience
        self.verbose = verbose
        self.best_val_loss = np.Inf
        self.wait = 0

    def on_epoch_end(self, epoch, logs={}):
        if not self.params['do_validation']:
            warnings.warn("Early stopping requires validation data!", RuntimeWarning)

        cur_val_loss = logs.get('val_loss')
        if cur_val_loss < self.best_val_loss:
            self.best_val_loss = cur_val_loss
            self.wait = 0
        else:
            if self.wait >= self.patience:
                if self.verbose > 0:
                    print("Epoch %05d: early stopping" % (epoch))
                self.model.stop_training = True
            self.wait += 1


class RemoteMonitor(Callback):
    def __init__(self, root='http://localhost:9000'):
        self.root = root
        self.seen = 0
        self.tot_loss = 0.
        self.tot_accuracy = 0.

    def on_epoch_begin(self, epoch, logs={}):
        self.seen = 0
        self.tot_loss = 0.
        self.tot_accuracy = 0.

    def on_batch_end(self, batch, logs={}):
        batch_size = logs.get('size', 0)
        self.seen += batch_size
        self.tot_loss += logs.get('loss', 0.) * batch_size
        if self.params['show_accuracy']:
            self.tot_accuracy += logs.get('accuracy', 0.) * batch_size

    def on_epoch_end(self, epoch, logs={}):
        import requests
        logs['epoch'] = epoch
        logs['loss'] = self.tot_loss / self.seen
        r = requests.post(self.root + '/publish/epoch/end/', {'data':json.dumps(logs)})
