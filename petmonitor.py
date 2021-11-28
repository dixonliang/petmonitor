from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import cv2
import argparse
import io
import re
import time
import twilio
import twilio.rest
import numpy as np
import picamera

from picar_4wd.servo import Servo # needed for servo control
from picar_4wd.pwm import PWM

from annotation import Annotator

from PIL import Image
from tflite_runtime.interpreter import Interpreter

# Set up Twilio
from twilio.rest import Client

# Twilio SID, authentication token, my phone number, and the Twilio phone number
# are stored as environment variables on my Pi so people can't see them
account_sid = ''
auth_token = ''
my_number = ''
twilio_number = ''

client = Client(account_sid,auth_token)


CAMERA_WIDTH = 500
CAMERA_HEIGHT = 500
threshold = 0.5 # set threshold for detection

ser = Servo(PWM("P0")) # reset angle for treats
ser.set_angle(90)


def load_labels(path): # use Coco 
  """Loads the labels file. Supports files with or without index numbers."""
  with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()
    labels = {}
    for row_number, content in enumerate(lines):
      pair = re.split(r'[:\s]+', content.strip(), maxsplit=1)
      if len(pair) == 2 and pair[0].strip().isdigit():
        labels[int(pair[0])] = pair[1].strip()
      else:
        labels[row_number] = pair[0].strip()
  return labels


def set_input_tensor(interpreter, image):
  """Sets the input tensor."""
  tensor_index = interpreter.get_input_details()[0]['index']
  input_tensor = interpreter.tensor(tensor_index)()[0]
  input_tensor[:, :] = image


def get_output_tensor(interpreter, index):
  """Returns the output tensor at the given index."""
  output_details = interpreter.get_output_details()[index]
  tensor = np.squeeze(interpreter.get_tensor(output_details['index']))
  return tensor


def annotate_objects(annotator, results, labels):
  """Draws the bounding box and label for each object in the results."""
  for obj in results:
    #if (labels[obj['class_id']] == 'dog'):
        # Convert the bounding box figures from relative coordinates
        # to absolute coordinates based on the original resolution
        ymin, xmin, ymax, xmax = obj['bounding_box']
        xmin = int(xmin * CAMERA_WIDTH)
        xmax = int(xmax * CAMERA_WIDTH)
        ymin = int(ymin * CAMERA_HEIGHT)
        ymax = int(ymax * CAMERA_HEIGHT)

        # Overlay the box, label, and score on the camera preview
        annotator.bounding_box([xmin, ymin, xmax, ymax])
        annotator.text([xmin, ymin],
                       '%s\n%.2f' % (labels[obj['class_id']], obj['score']))


def detect_objects(interpreter, image, threshold):
    
  """Returns a list of detection results, each a dictionary of object info."""
  set_input_tensor(interpreter, image)
  interpreter.invoke()

  # Get all output details
  boxes = get_output_tensor(interpreter, 0)
  classes = get_output_tensor(interpreter, 1)
  scores = get_output_tensor(interpreter, 2)
  count = int(get_output_tensor(interpreter, 3))

  results = []
  for i in range(count):
    if scores[i] >= threshold:
      result = {
          'bounding_box': boxes[i],
          'class_id': classes[i],
          'score': scores[i]
      }
      results.append(result)
  return results


def main():
  ## coordinates relative to frame for locations for Leo including his water bowl and bed
  ### bed coordinates ###
  bed_xmin = 0.15
  bed_xmax = 0.65
  bed_ymin = -0.10
  bed_ymax = 0.60
  ######################
  #### water bowl ####
  water_xmin = -0.10
  water_xmax = 0.85
  water_ymin = 0.45
  water_ymax = 0.95
  #######################
  ###### parameters ############################################
  day_time = 30 # set how many seconds are to elapse before sending a summary
  total_time_elapsed = 0 # how long the program has been running
  time_dog = 0 # time Leo on screen
  bed_time = 0 # time spent in bed
  water_time = 0 # time to measure if drinking water
  water_count = 0 # times Leo drank water
  total_sleep_time = 0 # total time Leo has slept
  currently_sleeping = False # check if Leo is sleeping
  done_sleeping = True # check if Leo was sleeping, default state is that he is not sleeping
  summary_sent = False # check if summary sent to make sure not spammed
  treats = False # check if treats have been released
  
  ###############################################################
  ##### load labels #############################################

  labels = load_labels('/home/pi/project/coco/coco_labels.txt')
  interpreter = Interpreter('/home/pi/project/coco/detect.tflite')
  interpreter.allocate_tensors()
  _, input_height, input_width, _ = interpreter.get_input_details()[0]['shape']
  
  ###############################################################

  camera = picamera.PiCamera()
  camera.resolution = (CAMERA_WIDTH,CAMERA_HEIGHT)
  camera.framerate = 2 #2 frames per second
  
  camera.start_preview()
  stream = io.BytesIO()
  annotator = Annotator(camera)
  
  for frame in camera.capture_continuous(
      stream, format='jpeg', use_video_port=True):
    stream.seek(0)
    image = Image.open(stream).convert('RGB').resize(
            (input_width, input_height), Image.ANTIALIAS)
    results = detect_objects(interpreter, image, threshold)
    
    total_time_elapsed = total_time_elapsed + 0.5
    
    for obj in results: # check if Leo has been detected on the screen
      if (labels[obj['class_id']] == 'dog' or labels[obj['class_id']] == 'cat' or labels[obj['class_id']] == 'teddy bear'): # possible classifications for Leo
         time_dog = time_dog + 0.5 # total time Leo has been detected during the day
         #print(obj['bounding_box'])
         
         if (obj['bounding_box'][0] > water_xmin and obj['bounding_box'][1] > water_ymin and  obj['bounding_box'][2] < water_xmax and obj['bounding_box'][3] < water_ymax): # check if he is drinking water
             water_time = water_time + 0.5
             if (water_time > 1.5): # if in same position for 3 frames likely drinking water
                 water_count = water_count + 1
                 message = client.messages.create( # send message that Leo has drank water
                 body = "Leo drank water!",
                 from_=twilio_number,
                 to=my_number
                  )
         if (obj['bounding_box'][0] > bed_xmin and obj['bounding_box'][1] > bed_ymin and  obj['bounding_box'][2] < bed_xmax and obj['bounding_box'][3] < bed_ymax): # check if Leo sleeping in bed
            bed_time = bed_time + 0.5
            total_sleep_time = total_sleep_time + 0.5
         else:
            if ((done_sleeping == False) and bed_time > 0):
             done_sleeping = True # set that he is done sleeping
             currently_sleeping = False # set back to default that he is not sleeping
             bed_time = 0 # clear if Leo not in bed
             message = client.messages.create( # send message to tell me that Leo is done sleeping
               body = "Leo is done sleeping!",
               from_=twilio_number,
               to=my_number
                )
        
            else:
             bed_time = 0 # clear if Leo not in bed
             water_time = 0 # clear water time
        
    
    if ((bed_time > 60) and (currently_sleeping == False)): # send message that Leo is sleeping if been in bed for longer than a minute that he is not sleeping
        currently_sleeping = True
        done_sleeping = False
        message = client.messages.create(
            body = "Leo is sleeping!",
            from_=twilio_number,
            to=my_number
            )
        
    if ((total_sleep_time > (day_time/4)) or ((day_time-time_dog) >  (day_time/4))): # if Leo has spent 1/4th of day sleeping on frame or off the frame, release treats
        ser = Servo(PWM("P0")) 
        ser.set_angle(0) # release treats
        treats = True # set release treats to true
        
    #####summary of the day################################################    
        
    if ((total_time_elapsed > day_time) and (summary_sent == False)): # summary at the end of the day
        summary_sent = True # set that summary sent
        message = client.messages.create(
        body = "Summary of Leo's day (minutes):"+
        "\nTime on Frame:"+str(time_dog/60)+
        "\nTime off Frame: "+str((day_time-time_dog)/60)+
        "\nTime Sleeping: "+str(total_sleep_time/60)+
        "\nWater Count: "+str(water_count)
        +"\nTreats Released: " + str(treats),
        from_=twilio_number,
        to=my_number
        )
        
    ########################################################################
        
         
    annotator.clear()
    annotate_objects(annotator, results, labels)
    annotator.update()

    stream.seek(0)
    stream.truncate()


if __name__ == '__main__':
  main()

